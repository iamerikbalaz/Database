"""Explicit, durable authorization before a recoverable filesystem identity change."""
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import Field, StringConstraints, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.material_review import _material, _require_generation, _state
from app.auth.access import AccessDependency, CATALOG_MANAGERS
from app.db.models import (MaterialAuditEvent, MaterialFileOperation, MaterialIdentityHistory, MaterialCollection,
                           MaterialNumberReservation, PBRMaterial, PBRMaterialMetadata, PublishedBrand)
from app.identity_client import IdentityClientError
from app.inventory_client import validate_relative_path
from app.material_identity import ACTIVE_STATUSES, identity_context, require_material_idle, lock_folder_catalog, require_folder_idle
from app.material_review import canonical_hash, invalidate_review
from app.material_naming import build_identity
from app.schemas import ApiSchema, CategoryCode, Sha256
from app.history_pagination import HistoryLimit, history_window


class IdentityPlanRequest(ApiSchema):
    target_brand_id: UUID
    main_category_code: CategoryCode
    target_parent: Annotated[str, StringConstraints(max_length=1792)] | None = None

    @field_validator("target_parent")
    @classmethod
    def relative_parent(cls, value):
        if value: validate_relative_path(value)
        return value


class IdentityConfirmRequest(IdentityPlanRequest):
    idempotency_key: UUID
    expected_generation: Annotated[int, Field(ge=0, strict=True)]
    expected_proposal_hash: Sha256
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    warnings_acknowledged: bool = False


def _conflict(code, message):
    raise HTTPException(409, {"code": code, "message": message})


def _context(material, brand):
    return {**identity_context(material), "brand_name": brand.name, "folder_prefix": brand.folder_prefix}


def _worker_request(proposal):
    return {"folder_path": proposal["source_context"]["folder_path"],
            "target_path": proposal["target_context"]["folder_path"],
            "brand_name": proposal["target_context"]["brand_name"],
            "material_name": proposal["target_context"]["material_name"]}


def _contexts(session, material, payload, *, lock=False):
    require_material_idle(session, material.id)
    if not material.folder_path: _conflict("IDENTITY_FOLDER_REQUIRED", "Link the source folder before planning an identity change.")
    if material.is_published: _conflict("PUBLISHED_IDENTITY_BLOCKED", "Published identity changes require a verified online importer contract.")
    if material.workflow_status != "IN_PROGRESS": _conflict("IDENTITY_REOPEN_REQUIRED", "Reopen the material before changing its identity.")
    ids = {material.published_brand_id, payload.target_brand_id}
    statement = select(PublishedBrand).where(PublishedBrand.id.in_(ids)).order_by(PublishedBrand.id)
    if lock: statement = statement.with_for_update()
    brands = {item.id: item for item in session.scalars(statement)}
    if payload.target_brand_id not in brands: raise HTTPException(404, "Published brand not found.")
    old_brand = brands[material.published_brand_id]; target = brands[payload.target_brand_id]
    if not target.is_active: _conflict("IDENTITY_BRAND_INACTIVE", "The target brand must be active.")
    rebrand = old_brand.id != target.id
    if rebrand and session.scalar(select(MaterialCollection.material_id).where(MaterialCollection.material_id == material.id).limit(1)):
        _conflict("IDENTITY_COLLECTIONS_ASSIGNED", "Remove the old brand's collection assignments before planning a rebrand.")
    number = target.next_sequence_number if rebrand else material.sequence_number
    if number > 9999: _conflict("IDENTITY_SEQUENCE_EXHAUSTED", "The target brand has no unused four-digit numbers.")
    try:
        identity = build_identity(target.folder_prefix, number, payload.main_category_code,
                                  material.material_name, source_identity=material.technical_identity)
    except ValueError:
        _conflict("MATERIAL_IDENTITY_INVALID", "The proposed folder identity is unsupported or exceeds 255 ASCII characters.")
    parent = payload.target_parent if payload.target_parent is not None else material.folder_path.rpartition("/")[0]
    folder = (parent + "/" if parent else "") + identity
    require_folder_idle(session, material.folder_path)
    require_folder_idle(session, folder)
    if folder == material.folder_path: _conflict("IDENTITY_UNCHANGED", "The proposed identity and folder are unchanged.")
    source_context = _context(material, old_brand)
    target_context = {**source_context, "published_brand_id": str(target.id), "brand_name": target.name,
        "folder_prefix": target.folder_prefix, "sequence_number": number, "main_category_code": payload.main_category_code,
        "technical_identity": identity, "folder_path": folder}
    # Another linked material must never be contained in either moving tree.
    for other in session.scalars(select(PBRMaterial).where(PBRMaterial.id != material.id)):
        if other.technical_identity == identity: _conflict("IDENTITY_ALREADY_USED", "The target identity is already allocated.")
        if other.folder_path:
            for path in (folder, material.folder_path):
                a, b = path.casefold(), other.folder_path.casefold()
                if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                    _conflict("IDENTITY_FOLDER_OVERLAP", "A linked material overlaps the proposed source or destination.")
    return {"source_context": source_context, "target_context": target_context,
            "generation": (_state(session, material.id).generation if _state(session, material.id) else 0),
            "reserves_number": rebrand}, target


def _view(operation):
    return {"id": str(operation.id), "material_id": str(operation.material_id), "actor_id": str(operation.actor_id),
            "status": operation.status, "source_context": operation.source_context, "target_context": operation.target_context,
            "proposal_hash": operation.proposal_hash, "reason": operation.request_payload["reason"],
            "result": operation.result, "created_at": operation.created_at, "updated_at": operation.updated_at}


def _existing(session, actor_id, material_id, payload, request_hash):
    operation = session.scalar(select(MaterialFileOperation).where(
        MaterialFileOperation.actor_id == actor_id, MaterialFileOperation.request_key == payload.idempotency_key))
    if operation and (operation.material_id != material_id or operation.request_hash != request_hash):
        _conflict("IDENTITY_REQUEST_KEY_REUSED", "The idempotency key belongs to a different request.")
    return operation


def _event(session, operation, state, event_type, result):
    session.add(MaterialAuditEvent(material_id=operation.material_id, actor_id=operation.actor_id,
        event_type=event_type, generation=state.generation, revision_hash=None,
        result={"audit": {"operation_id": str(operation.id), "reason": operation.request_payload["reason"],
                           "status": result.get("status", operation.status), "failure_code": result.get("failure_code")}}))


def _finish(database, operation_id, worker):
    # Authorization was durably recorded before source mutation. Completing that
    # transaction's consistency cannot depend on the initiating account staying active.
    with database.session() as session:
        operation = session.get(MaterialFileOperation, operation_id)
        if operation.status not in ACTIVE_STATUSES: return _view(operation)
        request = {**_worker_request({"source_context": operation.source_context, "target_context": operation.target_context}),
                   "operation_id": str(operation.id), "expected_plan_hash": operation.worker_plan["plan_hash"]}
    try:
        result = worker.execute(request).model_dump(mode="json")
    except IdentityClientError as exc:
        # A timeout/invalid response proves nothing about filesystem progress.
        # Keep ownership and retry the same durable journal; never allocate a new ID.
        with database.session() as session:
            operation = session.get(MaterialFileOperation, operation_id)
            return {**_view(operation), "retry_code": exc.code}
    with database.session() as session:
        material = session.scalar(select(PBRMaterial).where(PBRMaterial.id == operation.material_id).with_for_update())
        operation = session.scalar(select(MaterialFileOperation).where(MaterialFileOperation.id == operation_id).with_for_update())
        if operation.status not in ACTIVE_STATUSES: return _view(operation)
        state = _state(session, material.id, create=True)
        matches = all(identity_context(material)[key] == value for key, value in operation.source_context.items()
                      if key not in {"brand_name", "folder_prefix"})
        proof_matches = (result["operation_id"], result["plan_hash"], result["source_path"], result["target_path"]) == (
            str(operation.id), operation.worker_plan["plan_hash"], operation.source_context["folder_path"], operation.target_context["folder_path"])
        if result["status"] != "REJECTED":
            proof_matches = proof_matches and result["source_revision_hash"] == operation.worker_plan["source_revision_hash"]
        if not matches or not proof_matches or state.generation != operation.request_payload["expected_generation"] + 1:
            result = {"status": "RECOVERY_REQUIRED", "failure_code": "IDENTITY_DATABASE_CONFLICT"}
        if result["status"] == "COMPLETED":
            context = operation.target_context
            material.published_brand_id = UUID(context["published_brand_id"])
            material.sequence_number = context["sequence_number"]
            material.main_category_code = context["main_category_code"]
            material.technical_identity = context["technical_identity"]
            material.folder_path = context["folder_path"]
            material.validation_status = "NOT_CHECKED"
            metadata = session.get(PBRMaterialMetadata, material.id)
            for field in ("current_snapshot_id", "source_filename", "source_sha256", "source_content", "hex_color",
                          "width_cm", "height_cm", "master_resolution", "loaded_at"):
                setattr(metadata, field, None)
            metadata.status = "NOT_SCANNED"; metadata.warnings = []
            session.add(MaterialIdentityHistory(material_id=material.id, actor_id=operation.actor_id, operation_id=operation.id,
                old_context=operation.source_context, new_context=context, reason=operation.request_payload["reason"]))
        operation.status = result["status"]; operation.result = result
        state.failure_code = "IDENTITY_" + operation.status
        _event(session, operation, state, "IDENTITY_" + operation.status, result)
        session.commit(); session.refresh(operation)
        return _view(operation)


def build_material_identity_router(database, worker, *, mutations_enabled=False):
    router = APIRouter(prefix="/api/materials", tags=["material identity"])

    @router.get("/{material_id}/identity-operations")
    def operations(material_id: UUID, access: AccessDependency, after: UUID | None = None, limit: HistoryLimit = 100):
        with database.session() as session:
            access.check(session); _material(session, material_id, access)
            operations = history_window(session, MaterialFileOperation,
                conditions=(MaterialFileOperation.material_id == material_id,), after=after, limit=limit)
            return {"mutations_enabled": mutations_enabled, "operations": [_view(item) for item in operations]}

    @router.get("/{material_id}/identity-history")
    def history(material_id: UUID, access: AccessDependency, after: UUID | None = None, limit: HistoryLimit = 100):
        with database.session() as session:
            access.check(session); _material(session, material_id, access)
            history = history_window(session, MaterialIdentityHistory,
                conditions=(MaterialIdentityHistory.material_id == material_id,), after=after, limit=limit)
            return [{"id": item.id, "actor_id": item.actor_id, "operation_id": item.operation_id, "old_context": item.old_context,
                     "new_context": item.new_context, "reason": item.reason, "created_at": item.created_at} for item in history]

    def observe(material_id, payload, access):
        with database.session() as session:
            access.check(session, CATALOG_MANAGERS)
            material = _material(session, material_id, access)
            context, _ = _contexts(session, material, payload)
        try: plan = worker.plan(_worker_request(context)).model_dump(mode="json")
        except IdentityClientError as exc: raise HTTPException(503, {"code": exc.code, "message": str(exc)}) from None
        with database.session() as session:
            access.check(session, CATALOG_MANAGERS)
            material = _material(session, material_id, access, lock=True)
            latest, _ = _contexts(session, material, payload, lock=True)
            if context != latest: _conflict("IDENTITY_PROPOSAL_CHANGED", "Material or brand changed while the plan was being prepared.")
        proposal = {**context, "worker_plan": plan}
        return {**proposal, "proposal_hash": canonical_hash(proposal)}

    @router.post("/{material_id}/identity-plan")
    def plan(material_id: UUID, payload: IdentityPlanRequest, access: AccessDependency):
        return observe(material_id, payload, access)

    @router.post("/{material_id}/identity-confirm")
    def confirm(material_id: UUID, payload: IdentityConfirmRequest, access: AccessDependency):
        request_hash = canonical_hash({"material_id": str(material_id), "payload": payload.model_dump(mode="json")})
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS); _material(session, material_id, access)
            existing = _existing(session, actor.id, material_id, payload, request_hash)
            if existing: return _view(existing)  # Resume is explicit; replay does not restart a worker.
        if not mutations_enabled: raise HTTPException(503, {"code": "SOURCE_MUTATIONS_DISABLED"})
        try:
            proposal = observe(material_id, payload, access)
        except HTTPException as exc:
            if exc.status_code != 409: raise
            with database.session() as session:
                actor = access.check(session, CATALOG_MANAGERS); _material(session, material_id, access)
                existing = _existing(session, actor.id, material_id, payload, request_hash)
                if existing: return _view(existing)
            raise
        if proposal["proposal_hash"] != payload.expected_proposal_hash:
            _conflict("IDENTITY_PROPOSAL_CHANGED", "The plan changed. Review a fresh plan before confirmation.")
        if not proposal["worker_plan"]["ready"]: _conflict("IDENTITY_PLAN_BLOCKED", "Resolve the blocking plan findings first.")
        if proposal["worker_plan"]["warnings"] and not payload.warnings_acknowledged:
            _conflict("IDENTITY_WARNINGS_UNACKNOWLEDGED", "Acknowledge the plan warnings before confirmation.")
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS)
            material = _material(session, material_id, access, lock=True)
            existing = _existing(session, actor.id, material_id, payload, request_hash)
            if existing: return _view(existing)
            lock_folder_catalog(session)
            context, target = _contexts(session, material, payload, lock=True)
            if context != {key: proposal[key] for key in context}:
                _conflict("IDENTITY_PROPOSAL_CHANGED", "Material or brand changed before confirmation.")
            state = _state(session, material.id, create=True)
            _require_generation(state, payload.expected_generation)
            operation = MaterialFileOperation(id=uuid4(), material_id=material.id, actor_id=actor.id,
                source_brand_id=material.published_brand_id, target_brand_id=target.id,
                request_key=payload.idempotency_key, request_hash=request_hash, proposal_hash=proposal["proposal_hash"],
                request_payload=payload.model_dump(mode="json"), source_context=proposal["source_context"],
                target_context=proposal["target_context"], worker_plan=proposal["worker_plan"], status="RUNNING")
            session.add(operation)
            try:
                session.flush()
                if proposal["reserves_number"]:
                    session.add(MaterialNumberReservation(brand_id=target.id, sequence_number=target.next_sequence_number,
                        material_id=material.id, actor_id=actor.id, operation_id=operation.id))
                    target.next_sequence_number += 1
                invalidate_review(session, material, actor.id, "IDENTITY_STARTED", record_event=False)
                _event(session, operation, state, "IDENTITY_STARTED", {})
                session.commit()
            except IntegrityError:
                session.rollback()
                _conflict("IDENTITY_CONCURRENT_CONFLICT", "A concurrent allocation or identity operation conflicted with this request.")
        return _finish(database, operation.id, worker)

    @router.post("/{material_id}/identity-operations/{operation_id}/resume")
    def resume(material_id: UUID, operation_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session, CATALOG_MANAGERS); _material(session, material_id, access)
            operation = session.get(MaterialFileOperation, operation_id)
            if operation is None or operation.material_id != material_id: raise HTTPException(404, "Identity operation not found.")
            if operation.status not in ACTIVE_STATUSES: return _view(operation)
        if not mutations_enabled: raise HTTPException(503, {"code": "SOURCE_MUTATIONS_DISABLED"})
        return _finish(database, operation_id, worker)

    return router
