"""Explicit customer rename, optionally delegating source changes to identity journals."""
from datetime import UTC, datetime
import re
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.directory import _append_change, _check_revision, _folder_idle, _get, _require_key, _update_generated_name
from app.api.material_identity import _context, _event, _finish as finish_identity, _worker_request
from app.api.material_review import _state
from app.auth.access import AccessDependency, CATALOG_MANAGERS
from app.customer_orders import customer_view, directory_snapshot, folder_prefix_for_customer, require_available_customer_prefix
from app.db.customer_rename_models import CustomerRenameOperation
from app.db.models import MaterialFileOperation, MaterialIdentityHistory, PBRMaterial, Project, PublishedBrand
from app.identity_client import IdentityClientError
from app.material_identity import ACTIVE_STATUSES, lock_folder_catalog, require_brand_idle, require_folder_idle, require_material_idle
from app.material_naming import build_identity, material_name_from_identity
from app.material_review import canonical_hash, invalidate_review
from app.material_table import utc
from app.resource_commands import CommandKey
from app.schemas import ApiSchema, Name, Sha256


class RenamePlanRequest(ApiSchema):
    name: Name
    folder_prefix: Name | None = None
    rename_materials: bool = False
    expected_updated_at: datetime


class RenameRequest(RenamePlanRequest):
    confirmed: bool
    expected_proposal_hash: Sha256


def _conflict(code, message):
    raise HTTPException(409, {"code": code, "message": message})


def _read_context(session, customer_id, payload, *, lock=False):
    customer = _get(session, PublishedBrand, customer_id, lock=lock)
    _check_revision(customer, payload.expected_updated_at)
    require_brand_idle(session, customer.id)
    prefix = payload.folder_prefix or folder_prefix_for_customer(payload.name)
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{0,254}", prefix):
        raise HTTPException(422, "Folder prefix must contain uppercase letters, numbers and hyphens.")
    require_available_customer_prefix(session, prefix, customer.id)
    query = select(PBRMaterial).where(PBRMaterial.published_brand_id == customer.id).order_by(PBRMaterial.id)
    materials = list(session.scalars(query.with_for_update() if lock else query))
    orders_query = select(Project).where(Project.customer_id == customer.id).order_by(Project.id)
    orders = list(session.scalars(orders_query.with_for_update() if lock else orders_query))
    for order in orders:
        _folder_idle(session, order)
    rows = []
    all_materials = list(session.scalars(select(PBRMaterial))) if payload.rename_materials else []
    for material in materials:
        require_material_idle(session, material.id)
        source = _context(material, customer)
        target = dict(source)
        if payload.rename_materials:
            try:
                identity = build_identity(prefix, material.sequence_number, material.main_category_code,
                                          material.material_name, source_identity=material.technical_identity)
            except ValueError:
                _conflict("MATERIAL_IDENTITY_INVALID", "The proposed material identity is unsupported or too long.")
            folder = None if material.folder_path is None else (material.folder_path.rpartition("/")[0] + "/" if "/" in material.folder_path else "") + identity
            target.update(technical_identity=identity, folder_path=folder, brand_name=payload.name, folder_prefix=prefix,
                          material_name=material_name_from_identity(identity) or material.material_name)
            for other in all_materials:
                if other.id == material.id:
                    continue
                if other.technical_identity == identity:
                    _conflict("IDENTITY_ALREADY_USED", "A target material identity already exists.")
                if folder and other.folder_path:
                    for path in (folder, material.folder_path):
                        a, b = path.casefold(), other.folder_path.casefold()
                        if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                            _conflict("IDENTITY_FOLDER_OVERLAP", "A source or target folder overlaps another material.")
            if material.folder_path:
                require_folder_idle(session, material.folder_path)
                require_folder_idle(session, folder)
        state = _state(session, material.id)
        rows.append({"material_id": str(material.id), "source_context": source, "target_context": target,
            "changed": payload.rename_materials and source != target,
            "generation": state.generation if state else 0,
            "updated_at": material.updated_at.isoformat(), "is_published": material.is_published,
            "workflow_status": material.workflow_status})
    if customer.name == payload.name and customer.folder_prefix == prefix and not any(item["changed"] for item in rows):
        _conflict("CUSTOMER_RENAME_UNCHANGED", "The customer and selected material identities are already current.")
    context = {"customer": directory_snapshot(customer), "customer_updated_at": customer.updated_at.isoformat(),
        "name": payload.name, "folder_prefix": prefix, "rename_materials": payload.rename_materials,
        "materials": rows, "orders": [{"id": str(order.id), "updated_at": order.updated_at.isoformat()} for order in orders]}
    return context, customer, materials, orders


def _plan_view(proposal, mutations_enabled):
    affected = [item for item in proposal["materials"] if item["changed"]]
    issues = [{"material_id": item["material_id"], **finding} for item in affected
              for finding in item.get("worker_plan", {}).get("errors", [])]
    warnings = [{"material_id": item["material_id"], **finding} for item in affected
                for finding in item.get("worker_plan", {}).get("warnings", [])]
    if any(item["source_context"]["folder_path"] for item in affected) and not mutations_enabled:
        issues.append({"code": "SOURCE_MUTATIONS_DISABLED"})
    return {"proposal_hash": canonical_hash(proposal), "name": proposal["name"], "folder_prefix": proposal["folder_prefix"],
        "rename_materials": proposal["rename_materials"], "material_count": len(affected), "ready": not issues,
        "issues": issues, "warnings": warnings, "mutations_enabled": mutations_enabled,
        "affected_materials": [{"id": item["material_id"], "technical_identity": item["source_context"]["technical_identity"],
            "target_identity": item["target_context"]["technical_identity"], "folder_path": item["source_context"]["folder_path"],
            "target_path": item["target_context"]["folder_path"]} for item in affected]}


def _view(session, operation, *, sync_enabled=False):
    if canonical_hash(operation.authorization["proposal"]) != operation.proposal_hash:
        raise HTTPException(503, {"code": "CUSTOMER_RENAME_AUTHORIZATION_INVALID"})
    items = []
    for item in operation.authorization["items"]:
        child = session.get(MaterialFileOperation, UUID(item["operation_id"])) if item.get("operation_id") else None
        if item.get("operation_id") and (child is None or str(child.material_id) != item["material_id"]
                or child.source_brand_id != operation.customer_id or child.target_brand_id != operation.customer_id
                or child.request_payload.get("customer_rename_id") != str(operation.id)):
            raise HTTPException(503, {"code": "CUSTOMER_RENAME_AUTHORIZATION_INVALID"})
        items.append({"material_id": item["material_id"], "operation_id": item.get("operation_id"),
            "status": child.status if child else "COMPLETED",
            "failure_code": (child.result or {}).get("failure_code") if child else None})
    return {"id": str(operation.id), "customer_id": str(operation.customer_id), "status": operation.status,
        "completed_count": sum(item["status"] == "COMPLETED" for item in items), "total_count": len(items),
        "materials": items, "customer": customer_view(session, session.get(PublishedBrand, operation.customer_id), sync_enabled=sync_enabled),
        "created_at": utc(operation.created_at).isoformat(), "updated_at": utc(operation.updated_at).isoformat()}


def _existing(session, actor_id, key, customer_id, request_hash):
    operation = session.scalar(select(CustomerRenameOperation).where(CustomerRenameOperation.actor_id == actor_id,
                                                                   CustomerRenameOperation.request_key == key))
    if operation and (operation.customer_id != customer_id or operation.request_hash != request_hash):
        _conflict("CUSTOMER_RENAME_KEY_REUSED", "This request key belongs to another command.")
    return operation


def finish_customer_rename(database, operation_id, worker, *, sync_enabled=False):
    with database.session() as session:
        operation = session.get(CustomerRenameOperation, operation_id)
        if operation.status not in ACTIVE_STATUSES:
            return _view(session, operation, sync_enabled=sync_enabled)
        _view(session, operation, sync_enabled=sync_enabled)  # Verify durable child ownership before dispatch.
        child_ids = [UUID(item["operation_id"]) for item in operation.authorization["items"] if item.get("operation_id")]
    for child_id in child_ids:
        result = finish_identity(database, child_id, worker)
        if result["status"] in ACTIVE_STATUSES:
            break  # Keep exact journals and claims; retry is explicit.
    with database.session() as session:
        operation = session.scalar(select(CustomerRenameOperation).where(CustomerRenameOperation.id == operation_id).with_for_update())
        if operation.status in ACTIVE_STATUSES:
            view = _view(session, operation, sync_enabled=sync_enabled)
            states = {item["status"] for item in view["materials"]}
            operation.status = "RECOVERY_REQUIRED" if states.intersection(ACTIVE_STATUSES) else "PARTIAL" if states - {"COMPLETED"} else "COMPLETED"
            operation.result = {"completed_count": view["completed_count"], "total_count": view["total_count"], "materials": view["materials"]}
            operation.updated_at = datetime.now(UTC)
            session.commit()
        return _view(session, operation, sync_enabled=sync_enabled)


def build_customer_rename_router(database, worker, settings):
    router = APIRouter(prefix="/api/customers", tags=["customer rename"])
    enabled = settings.source_mutations_enabled

    def observe(customer_id, payload, access):
        with database.session() as session:
            access.check(session, CATALOG_MANAGERS)
            context, _, _, _ = _read_context(session, customer_id, payload)
        proposal = {**context, "materials": [dict(item) for item in context["materials"]]}
        for item in proposal["materials"]:
            if item["changed"] and item["source_context"]["folder_path"]:
                try:
                    item["worker_plan"] = worker.plan(_worker_request(item)).model_dump(mode="json")
                except IdentityClientError as error:
                    raise HTTPException(503, {"code": error.code, "material_id": item["material_id"]}) from None
        with database.session() as session:
            access.check(session, CATALOG_MANAGERS)
            latest, _, _, _ = _read_context(session, customer_id, payload)
            if latest != context:
                _conflict("CUSTOMER_RENAME_CHANGED", "The customer or its materials changed while preparing this operation.")
        return proposal, context

    @router.post("/{customer_id}/rename-plan")
    def plan(customer_id: UUID, payload: RenamePlanRequest, access: AccessDependency):
        proposal, _ = observe(customer_id, payload, access)
        return _plan_view(proposal, enabled)

    @router.get("/{customer_id}/rename-operations")
    def operations(customer_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session, CATALOG_MANAGERS)
            _get(session, PublishedBrand, customer_id)
            rows = session.scalars(select(CustomerRenameOperation).where(CustomerRenameOperation.customer_id == customer_id)
                                   .order_by(CustomerRenameOperation.created_at.desc()).limit(100))
            return [_view(session, operation, sync_enabled=settings.notion_outbound_enabled) for operation in rows]

    @router.post("/{customer_id}/rename")
    def rename(customer_id: UUID, payload: RenameRequest, access: AccessDependency, request_key: CommandKey = None):
        _require_key(request_key)
        if not payload.confirmed:
            raise HTTPException(422, "Explicit confirmation is required.")
        request_hash = canonical_hash({"customer_id": str(customer_id), "payload": payload.model_dump(mode="json")})
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS)
            if previous := _existing(session, actor.id, request_key, customer_id, request_hash):
                return _view(session, previous, sync_enabled=settings.notion_outbound_enabled)
        try:
            proposal, original = observe(customer_id, payload, access)
        except HTTPException as error:
            if error.status_code != 409:
                raise
            # An overlapping identical request may commit while this request
            # is observing the filesystem. Its durable receipt wins over the
            # now-stale input revision, without dispatching a second worker.
            with database.session() as session:
                actor = access.check(session, CATALOG_MANAGERS)
                if previous := _existing(session, actor.id, request_key, customer_id, request_hash):
                    return _view(session, previous, sync_enabled=settings.notion_outbound_enabled)
            raise
        if canonical_hash(proposal) != payload.expected_proposal_hash:
            _conflict("CUSTOMER_RENAME_CHANGED", "The rename plan changed. Review and confirm again.")
        if not _plan_view(proposal, enabled)["ready"]:
            _conflict("CUSTOMER_RENAME_BLOCKED", "Resolve the reported rename issues first.")
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS, exclusive=True)
            if previous := _existing(session, actor.id, request_key, customer_id, request_hash):
                return _view(session, previous, sync_enabled=settings.notion_outbound_enabled)
            context, customer, materials, orders = _read_context(session, customer_id, payload, lock=True)
            lock_folder_catalog(session)
            if context != original:
                _conflict("CUSTOMER_RENAME_CHANGED", "The customer or materials changed before confirmation.")
            batch_id = uuid4()
            items = []
            for material, item in zip(materials, proposal["materials"], strict=True):
                material.source_brand_name = material.source_brand_name or customer.name
                if not item["changed"]:
                    continue
                source, target = item["source_context"], item["target_context"]
                if material.folder_path:
                    state = _state(session, material.id, create=True)
                    child_id = uuid4()
                    authorization = {"customer_rename_id": str(batch_id), "expected_generation": state.generation,
                                     "reason": "Confirmed customer rename"}
                    child = MaterialFileOperation(id=child_id, material_id=material.id, actor_id=actor.id,
                        source_brand_id=customer.id, target_brand_id=customer.id, request_key=uuid4(),
                        request_hash=canonical_hash(authorization), proposal_hash=canonical_hash(item),
                        request_payload=authorization, source_context=source, target_context=target,
                        worker_plan=item["worker_plan"], status="RUNNING")
                    session.add(child)
                    invalidate_review(session, material, actor.id, "IDENTITY_STARTED", record_event=False)
                    _event(session, child, state, "IDENTITY_STARTED", {})
                    items.append({"material_id": str(material.id), "operation_id": str(child_id)})
                else:
                    material.technical_identity = target["technical_identity"]
                    material.material_name = target["material_name"]
                    material.source_brand_name = target["brand_name"]
                    material.is_published = False
                    material.publication_status = "NOT_PUBLISHED"
                    invalidate_review(session, material, actor.id, "CUSTOMER_RENAMED")
                    session.add(MaterialIdentityHistory(material_id=material.id, actor_id=actor.id, old_context=source,
                        new_context=target, reason="Confirmed customer rename (no source folder linked)"))
                    items.append({"material_id": str(material.id)})
            before = directory_snapshot(customer)
            customer.name = proposal["name"]
            customer.folder_prefix = proposal["folder_prefix"]
            customer.updated_at = datetime.now(UTC)
            _append_change(session, customer, actor.id, before, "RENAMED")
            from app.notion_outbound import enqueue_customer_sync, enqueue_order_sync
            enqueue_customer_sync(session, customer, actor.id)
            for order in orders:
                before_order = directory_snapshot(order)
                _update_generated_name(session, order)
                if directory_snapshot(order) != before_order:
                    order.updated_at = datetime.now(UTC)
                    _append_change(session, order, actor.id, before_order, "UPDATED")
                    enqueue_order_sync(session, order, actor.id)
            operation = CustomerRenameOperation(id=batch_id, customer_id=customer.id, actor_id=actor.id,
                request_key=request_key, request_hash=request_hash, proposal_hash=payload.expected_proposal_hash,
                authorization={"proposal": proposal, "items": items}, status="RUNNING")
            session.add(operation)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                _conflict("CUSTOMER_RENAME_CONFLICT", "A concurrent rename or identity allocation conflicted with this operation.")
        return finish_customer_rename(database, batch_id, worker, sync_enabled=settings.notion_outbound_enabled)

    @router.post("/{customer_id}/rename-operations/{operation_id}/resume")
    def resume(customer_id: UUID, operation_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session, CATALOG_MANAGERS)
            operation = session.get(CustomerRenameOperation, operation_id)
            if operation is None or operation.customer_id != customer_id:
                raise HTTPException(404, "Customer rename operation not found.")
            if operation.status not in ACTIVE_STATUSES:
                return _view(session, operation, sync_enabled=settings.notion_outbound_enabled)
            if any(item.get("operation_id") for item in operation.authorization["items"]) and not enabled:
                raise HTTPException(503, {"code": "SOURCE_MUTATIONS_DISABLED"})
        return finish_customer_rename(database, operation_id, worker, sync_enabled=settings.notion_outbound_enabled)

    return router
