"""Authenticated plan/confirm/recover endpoints for local PREVIEW file edits."""
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4, uuid5

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.material_review import _material, _state
from app.auth.access import AccessDependency, MATERIAL_EDITORS
from app.db.models import MaterialAuditEvent
from app.db.preview_edit_models import PreviewEditOperation, PreviewEditOwner, TERMINAL
from app.local_filesystem import LocalFilesError
from app.local_preview_edits import LocalPreviewEdits
from app.material_identity import require_material_idle, lock_folder_catalog, require_folder_idle
from app.material_review import canonical_hash, invalidate_review
from app.material_table import utc
from app.preview_edits import PreviewEditPlanRequest, PreviewEditApplyRequest, PreviewEditResume, changes_for
from app.resource_history import append_resource_change, resource_snapshot

WARNINGS = ["CHECKED_RESET", "AUTOMATIC_CHECK_RESET", "PUBLISHED_RESET"]


def operation_view(operation):
    return {"id": str(operation.id), "status": operation.status, "items": operation.items,
        "total_renames": sum(item.get("renamed", 0) for item in operation.items),
        "total_deletes": sum(item.get("deleted", 0) for item in operation.items),
        "warnings": WARNINGS, "quarantine_retained": True}


def build_preview_edits_router(database, settings, library=None):
    router = APIRouter(prefix="/api/material-preview-edits", tags=["preview file edits"])
    adapter = LocalPreviewEdits(library) if library is not None else None

    def local(request, *, mutation=False):
        if adapter is None: raise HTTPException(503, {"code": "LOCAL_DESKTOP_UNAVAILABLE"})
        if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(403, {"code": "LOCAL_DESKTOP_ONLY"})
        if mutation and not settings.source_mutations_enabled:
            raise HTTPException(403, {"code": "SOURCE_MUTATIONS_DISABLED"})

    def authorized(session, identifier, access, *, lock=False):
        actor = access.check(session, MATERIAL_EDITORS)
        query = select(PreviewEditOperation).where(PreviewEditOperation.id == identifier)
        if lock: query = query.with_for_update()
        operation = session.scalar(query)
        if operation is None or (operation.actor_id != actor.id and actor.role != "ADMIN"):
            raise HTTPException(404, "Preview edit operation not found.")
        for item in operation.plan["items"]:
            _material(session, UUID(item["material_id"]), access, historical=operation.status in TERMINAL)
        return operation

    def prepare(session, payload, access):
        actor = access.check(session, MATERIAL_EDITORS)
        selector = payload.model_dump(mode="json", include={"action", "filename", "new_name", "find", "replace", "delete_containing", "case_sensitive"})
        selected = sorted(payload.materials, key=lambda item: str(item.id))
        materials = {item.id: _material(session, item.id, access, lock=True) for item in selected}
        lock_folder_catalog(session)
        views = []; frozen = []
        for selection in selected:
            material = materials[selection.id]
            require_material_idle(session, material.id)
            if utc(material.updated_at) != utc(selection.expected_updated_at):
                raise HTTPException(409, {"code": "PREVIEW_MATERIAL_CHANGED", "message": "Reload the selected materials before planning."})
            errors = []; snapshot = None; changes = []
            if not material.folder_path:
                errors.append({"code": "FOLDER_REQUIRED", "message": "The material has no linked data folder."})
            else:
                access.require_folder(material.folder_path, material.technical_identity)
                require_folder_idle(session, material.folder_path)
                try: snapshot = adapter.snapshot(material.folder_path)
                except (LocalFilesError, OSError, ValueError) as error:
                    errors.append({"code": error.code if isinstance(error, LocalFilesError) else "PREVIEW_UNAVAILABLE",
                        "message": "PREVIEW could not be safely read. Check the folder, file permissions and file sizes."})
                if snapshot is not None: changes, errors = changes_for(snapshot, selector)
            renamed = sum(change["to"] is not None for change in changes)
            deleted = sum(change["to"] is None for change in changes)
            views.append({"material_id": str(material.id), "identity": material.technical_identity,
                "rename_count": renamed, "delete_count": deleted, "changes": changes, "issues": errors})
            frozen.append({"material_id": str(material.id), "identity": material.technical_identity,
                "brand_id": str(material.published_brand_id), "folder_path": material.folder_path,
                "updated_at": utc(material.updated_at).isoformat(), "snapshot": snapshot, "changes": changes})
        plan = {"selector": selector, "items": frozen}
        proposal = canonical_hash(plan)
        renames = sum(item["rename_count"] for item in views); deletes = sum(item["delete_count"] for item in views)
        return actor, {"proposal_hash": proposal, "can_apply": bool(renames + deletes) and not any(item["issues"] for item in views),
            "total_renames": renames, "total_deletes": deletes, "items": views, "warnings": WARNINGS}, plan, materials

    @router.post("/plan")
    def plan(payload: PreviewEditPlanRequest, access: AccessDependency, request: Request):
        local(request, mutation=True)
        with database.session() as session: return prepare(session, payload, access)[1]

    @router.get("")
    def active(access: AccessDependency, request: Request, material_id: UUID | None = None,
               material_ids: Annotated[list[UUID] | None, Query(max_length=100)] = None):
        local(request)
        with database.session() as session:
            actor = access.check(session, MATERIAL_EDITORS)
            selected = set(material_ids or [])
            if material_id is not None: selected.add(material_id)
            if len(selected) > 100: raise HTTPException(422, {"code": "PREVIEW_SELECTION_LIMIT"})
            for identifier in sorted(selected): _material(session, identifier, access)
            query = select(PreviewEditOperation).where(PreviewEditOperation.status.in_({"RUNNING", "RECOVERY_REQUIRED"}))
            if actor.role != "ADMIN": query = query.where(PreviewEditOperation.actor_id == actor.id)
            if selected:
                query = query.join(PreviewEditOwner).where(PreviewEditOwner.material_id.in_(selected)).distinct()
            rows = session.scalars(query.order_by(PreviewEditOperation.created_at.desc()).limit(100 if selected else 30)).all()
            return {"items": [operation_view(authorized(session, row.id, access)) for row in rows]}

    @router.get("/{identifier}")
    def get_operation(identifier: UUID, access: AccessDependency, request: Request):
        local(request)
        with database.session() as session: return operation_view(authorized(session, identifier, access))

    def finish(identifier, access):
        with database.session() as session:
            operation = authorized(session, identifier, access)
            if operation.status in TERMINAL: return operation_view(operation)
            count = len(operation.plan["items"])
        for index in range(count):
            with database.session() as session:
                operation = authorized(session, identifier, access, lock=True)
                if operation.status in TERMINAL: return operation_view(operation)
                current = operation.items[index]
                if current["status"] in {"COMPLETED", "REJECTED"}: continue
                frozen = operation.plan["items"][index]
                material = _material(session, UUID(frozen["material_id"]), access, lock=True)
                require_material_idle(session, material.id, preview_operation_id=operation.id)
                owner = session.get(PreviewEditOwner, material.id)
                if owner is None or owner.operation_id != operation.id:
                    raise HTTPException(409, {"code": "PREVIEW_OWNERSHIP_CHANGED"})
                lock_folder_catalog(session)
                require_folder_idle(session, frozen["folder_path"], preview_operation_id=operation.id)
                if (material.folder_path != frozen["folder_path"] or material.technical_identity != frozen["identity"]
                        or str(material.published_brand_id) != frozen["brand_id"]):
                    raise HTTPException(409, {"code": "PREVIEW_MATERIAL_CHANGED"})
                try:
                    result = adapter.execute(operation.id, frozen)
                except (LocalFilesError, OSError, ValueError) as error:
                    result = {"status": "RECOVERY_REQUIRED", "error_code": error.code if isinstance(error, LocalFilesError) else "PREVIEW_EDIT_IO_INTERRUPTED"}
                item = {**current, **result, "updated_at": utc(material.updated_at).isoformat()}
                if result["status"] in {"COMPLETED", "REJECTED"}:
                    state = _state(session, material.id, create=True)
                    session.add(MaterialAuditEvent(material_id=material.id, actor_id=access.user.id,
                        event_type="PREVIEW_FILES_EDITED" if result["status"] == "COMPLETED" else "PREVIEW_FILES_REJECTED",
                        request_key=uuid5(operation.id, str(material.id) + ":finished"), request_hash=operation.request_hash,
                        generation=state.generation, revision_hash=state.revision_hash,
                        result={"body": item, "audit": {"operation_id": str(operation.id), "changes": frozen["changes"],
                            "quarantine_retained": True, "authorized_actor_id": str(operation.actor_id)}}))
                    session.delete(owner)
                items = [dict(value) for value in operation.items]; items[index] = item
                operation.items = items
                if any(value["status"] in {"RUNNING", "RECOVERY_REQUIRED"} for value in items): operation.status = "RECOVERY_REQUIRED"
                elif all(value["status"] == "COMPLETED" for value in items): operation.status = "COMPLETED"
                elif all(value["status"] == "REJECTED" for value in items): operation.status = "REJECTED"
                else: operation.status = "PARTIAL"
                session.commit()
        with database.session() as session: return operation_view(authorized(session, identifier, access))

    @router.post("")
    def apply(payload: PreviewEditApplyRequest, access: AccessDependency, request: Request):
        local(request, mutation=True); request_hash = canonical_hash(payload.model_dump(mode="json"))
        with database.session() as session:
            actor = access.check(session, MATERIAL_EDITORS)
            operation = session.scalar(select(PreviewEditOperation).where(PreviewEditOperation.actor_id == actor.id,
                PreviewEditOperation.request_key == payload.idempotency_key).with_for_update())
            if operation is not None:
                if operation.request_hash != request_hash: raise HTTPException(409, {"code": "PREVIEW_REQUEST_KEY_REUSED"})
                identifier = operation.id
            else:
                actor, preview, frozen, materials = prepare(session, payload, access)
                if preview["proposal_hash"] != payload.expected_proposal_hash:
                    raise HTTPException(409, {"code": "PREVIEW_PLAN_CHANGED", "message": "The files or material changed. Review a new plan."})
                if not preview["can_apply"]: raise HTTPException(409, {"code": "PREVIEW_PLAN_BLOCKED", "plan": preview})
                identifier = uuid4(); items = []
                for planned in frozen["items"]:
                    material = materials[UUID(planned["material_id"])]
                    changed = bool(planned["changes"])
                    if changed:
                        before = resource_snapshot(material)
                        _state(session, material.id, create=True)
                        invalidate_review(session, material, actor.id, "PREVIEW_EDIT_STARTED", record_event=False)
                        material.is_published = False; material.publication_status = "NOT_PUBLISHED"
                        material.updated_at = datetime.now(UTC)
                        append_resource_change(session, material, actor.id, before)
                    items.append({"material_id": planned["material_id"], "identity": planned["identity"],
                        "status": "RUNNING" if changed else "COMPLETED", "renamed": 0, "deleted": 0,
                        "error_code": None, "updated_at": utc(material.updated_at).isoformat(), "warnings": WARNINGS if changed else []})
                operation = PreviewEditOperation(id=identifier, actor_id=actor.id, request_key=payload.idempotency_key,
                    request_hash=request_hash, proposal_hash=preview["proposal_hash"], request_payload=payload.model_dump(mode="json"),
                    plan=frozen, items=items, status="RUNNING")
                try:
                    session.add(operation); session.flush()
                    for planned in frozen["items"]:
                        if not planned["changes"]: continue
                        session.add(PreviewEditOwner(material_id=UUID(planned["material_id"]), operation_id=identifier,
                            brand_id=UUID(planned["brand_id"]), folder_path=planned["folder_path"]))
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    raise HTTPException(409, {"code": "PREVIEW_OPERATION_CONFLICT"}) from None
        return finish(identifier, access)

    @router.post("/{identifier}/resume")
    def resume(identifier: UUID, payload: PreviewEditResume, access: AccessDependency, request: Request):
        local(request, mutation=True)
        return finish(identifier, access)

    return router
