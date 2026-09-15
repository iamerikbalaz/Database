"""Authenticated observed inventories and explicit audited reopening."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.access import AccessDependency, CATALOG_MANAGERS, MATERIAL_EDITORS
from app.auth.service import database_now
from app.db.models import MaterialAuditEvent, MaterialInventory, MaterialReviewState, PBRMaterial, PBRMaterialMetadata
from app.inventory_client import InventoryClient, InventoryClientError
from app.material_review import canonical_hash, invalidate_review, material_context, read_review
from app.schemas import ApiSchema


class ReviewMutation(ApiSchema):
    idempotency_key: UUID
    expected_generation: Annotated[int, Field(ge=0, strict=True)]


class ReopenRequest(ReviewMutation):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


def _material(session, material_id, access, *, lock=False):
    statement = select(PBRMaterial).where(PBRMaterial.id == material_id)
    if lock: statement = statement.with_for_update()
    material = session.scalar(statement)
    if material is None: raise HTTPException(404, "PBR material not found.")
    access.require_material(material)
    return material


def _state(session, material_id, *, create=False):
    state = session.get(MaterialReviewState, material_id)
    if state is None and create:
        state = MaterialReviewState(material_id=material_id, generation=0)
        session.add(state)
        session.flush()
    return state


def _request_hash(operation, material_id, payload):
    return canonical_hash({"operation": operation, "material_id": str(material_id), "payload": payload.model_dump(mode="json")})


def _replay(session, actor_id, material_id, payload, request_hash):
    event = session.scalar(select(MaterialAuditEvent).where(
        MaterialAuditEvent.actor_id == actor_id, MaterialAuditEvent.request_key == payload.idempotency_key))
    if event is None: return None
    if event.material_id != material_id or event.request_hash != request_hash:
        raise HTTPException(409, "Idempotency key was already used for a different request.")
    return JSONResponse(status_code=event.result["status_code"], content=event.result["body"])


def _require_generation(state, expected):
    if (state.generation if state else 0) != expected:
        raise HTTPException(409, "Material review changed. Reload before retrying.")


def _record(session, material, state, actor_id, operation, payload, request_hash, body, code=200, audit=None):
    session.add(MaterialAuditEvent(material_id=material.id, actor_id=actor_id,
        event_type=operation, generation=state.generation, revision_hash=state.revision_hash,
        request_key=payload.idempotency_key, request_hash=request_hash,
        result={"status_code": code, "body": body, "audit": audit or {}}))
    try: session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Concurrent review operation conflicted with this request.") from None
    return JSONResponse(status_code=code, content=body)


def build_material_review_router(database, inventory_client: InventoryClient):
    router = APIRouter(prefix="/api/materials", tags=["material review"])

    @router.get("/{material_id}/review")
    def get_review(material_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session)
            _material(session, material_id, access)
            return read_review(_state(session, material_id))

    @router.get("/{material_id}/inventory")
    def get_inventory(material_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session)
            _material(session, material_id, access)
            state = _state(session, material_id)
            inventory = session.get(MaterialInventory, state.inventory_id) if state and state.inventory_id else None
            return {"review": read_review(state), "inventory": inventory.source_inventory if inventory else None}

    @router.get("/{material_id}/audit")
    def get_audit(material_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session)
            _material(session, material_id, access)
            events = session.scalars(select(MaterialAuditEvent).where(MaterialAuditEvent.material_id == material_id)
                                     .order_by(MaterialAuditEvent.created_at.desc(), MaterialAuditEvent.id.desc()).limit(100))
            return [{"id": item.id, "actor_id": item.actor_id, "event_type": item.event_type,
                     "generation": item.generation, "revision_hash": item.revision_hash, "created_at": item.created_at,
                     "details": item.result.get("audit", {})} for item in events]

    @router.post("/{material_id}/inventory/scan")
    def scan_inventory(material_id: UUID, payload: ReviewMutation, access: AccessDependency):
        request_hash = _request_hash("INVENTORY_SCAN", material_id, payload)
        with database.session() as session:
            actor = access.check(session, MATERIAL_EDITORS)
            material = _material(session, material_id, access)
            replay = _replay(session, actor.id, material_id, payload, request_hash)
            if replay is not None: return replay
            _require_generation(_state(session, material_id), payload.expected_generation)
            if not material.folder_path: raise HTTPException(409, "Link a material folder before scanning its inventory.")
            context = material_context(material)
            expected = (context, material.workflow_status, material.updated_at)
        inventory = None; failure = None
        try:
            inventory = inventory_client.inventory(context["folder_path"])
            if inventory.folder_name != context["technical_identity"]:
                raise InventoryClientError("INVENTORY_SOURCE_CHANGED")
        except InventoryClientError as exc:
            failure = exc.code
        with database.session() as session:
            actor = access.check(session, MATERIAL_EDITORS)
            material = _material(session, material_id, access, lock=True)
            replay = _replay(session, actor.id, material_id, payload, request_hash)
            if replay is not None: return replay
            state = _state(session, material_id, create=True)
            _require_generation(state, payload.expected_generation)
            if (material_context(material), material.workflow_status, material.updated_at) != expected:
                raise HTTPException(409, "Material changed while inventory was being scanned.")
            now = database_now(session)
            if failure:
                invalidate_review(session, material, actor.id, failure, record_event=False)
                state.checked_at = now
                body = {"detail": {"code": failure, "message": "Source inventory could not be verified."}, "review": read_review(state)}
                return _record(session, material, state, actor.id, "INVENTORY_FAILED", payload, request_hash, body, 503 if failure == "INVENTORY_UNAVAILABLE" else 422)
            revision_hash = canonical_hash({"source_files_hash": inventory.source_revision_hash, "material_context": context})
            if state.revision_hash != revision_hash:
                invalidate_review(session, material, actor.id, "SOURCE_REVISION_CHANGED", record_event=False)
            snapshot = MaterialInventory(material_id=material.id, actor_id=actor.id, generation=state.generation,
                revision_hash=revision_hash, source_inventory=inventory.model_dump(mode="json"), material_context=context)
            session.add(snapshot); session.flush()
            state.revision_hash = revision_hash; state.inventory_id = snapshot.id
            state.checked_at = now; state.failure_code = None
            return _record(session, material, state, actor.id, "INVENTORY_SCANNED", payload, request_hash, read_review(state))

    @router.post("/{material_id}/reopen")
    def reopen(material_id: UUID, payload: ReopenRequest, access: AccessDependency):
        request_hash = _request_hash("REOPEN", material_id, payload)
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS)
            material = _material(session, material_id, access, lock=True)
            replay = _replay(session, actor.id, material_id, payload, request_hash)
            if replay is not None: return replay
            state = _state(session, material_id, create=True)
            _require_generation(state, payload.expected_generation)
            if material.workflow_status != "DONE": raise HTTPException(409, "Only a DONE material can be reopened.")
            invalidate_review(session, material, actor.id, "REOPENED", record_event=False)
            material.workflow_status = "IN_PROGRESS"; material.validation_status = "NOT_CHECKED"
            metadata = session.get(PBRMaterialMetadata, material_id)
            if metadata is None: raise HTTPException(500, "Material metadata state is missing.")
            for field in ("current_snapshot_id", "source_filename", "source_sha256", "source_content", "hex_color", "width_cm", "height_cm", "master_resolution", "loaded_at"):
                setattr(metadata, field, None)
            metadata.status = "NOT_SCANNED"; metadata.warnings = []
            body = {"workflow_status": "IN_PROGRESS", "review": read_review(state)}
            return _record(session, material, state, actor.id, "REOPENED", payload, request_hash, body, audit={"reason": payload.reason})

    return router
