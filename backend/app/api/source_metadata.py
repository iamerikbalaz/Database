"""Authorized root metadata edits with durable exact-request recovery."""
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.material_review import _material, _state
from app.auth.access import AccessDependency, MATERIAL_EDITORS
from app.auth.service import _aware
from app.db.models import MaterialAuditEvent, MaterialMetadataOperation, PBRMaterial, PBRMaterialMetadata
from app.material_identity import lock_folder_catalog, require_folder_idle, require_material_idle
from app.material_review import canonical_hash, invalidate_review
from app.metadata_client import MetadataClientError, MetadataValues
from app.metadata_saves import persist_metadata_snapshot
from app.schemas import ApiSchema, Sha256


class MetadataSaveRequest(ApiSchema):
    idempotency_key: UUID
    expected_updated_at: datetime
    expected_sha256: Sha256 | None
    values: MetadataValues


def _operation_view(operation):
    return {"id": str(operation.id), "status": operation.status, "result": operation.result}


def _values(current):
    return {field: (str(getattr(current, field)) if current is not None and getattr(current, field) is not None else None)
            for field in MetadataValues.model_fields}


def _finish(database, operation_id, worker):
    with database.session() as session:
        operation = session.get(MaterialMetadataOperation, operation_id)
        if operation.status != "RUNNING": return _operation_view(operation)
        request = {"operation_id": str(operation.id), "folder_path": operation.folder_path,
                   "expected_sha256": operation.request_payload["expected_sha256"],
                   "values": operation.request_payload["values"]}
    try: outcome = worker.execute(request)
    except MetadataClientError:
        # Even a worker 409/503 can follow source mutation; keep durable ownership.
        with database.session() as session: return _operation_view(session.get(MaterialMetadataOperation, operation_id))
    with database.session() as session:
        material = session.scalar(select(PBRMaterial).where(PBRMaterial.id == operation.material_id).with_for_update())
        operation = session.scalar(select(MaterialMetadataOperation).where(MaterialMetadataOperation.id == operation_id).with_for_update())
        if operation.status != "RUNNING": return _operation_view(operation)
        if material.folder_path != operation.folder_path or outcome.operation_id != str(operation.id):
            return _operation_view(operation)
        if outcome.status == "COMPLETED":
            source = outcome.metadata
            if (source is None or source.folder_name != material.technical_identity
                    or MetadataValues(**_values(source)).model_dump() != operation.request_payload["values"]):
                return _operation_view(operation)
            warnings = [] if source.status == "VALID" else [{"code": "SOURCE_METADATA_PARTIAL", "message": "Some editable source metadata values are missing.", "path": "metadata.txt"}]
            persist_metadata_snapshot(session, material.id, _values(source), status=source.status,
                source_filename="metadata.txt", source_sha256=source.sha256, source_content=source.raw_content,
                warnings=warnings)
        operation.status = outcome.status
        operation.result = {"failure_code": outcome.failure_code,
            "sha256": outcome.metadata.sha256 if outcome.metadata else None,
            "values": _values(outcome.metadata) if outcome.metadata else None}
        state = _state(session, material.id, create=True)
        session.add(MaterialAuditEvent(material_id=material.id, actor_id=operation.actor_id,
            event_type="SOURCE_METADATA_" + outcome.status, generation=state.generation, revision_hash=None,
            result={"audit": {"operation_id": str(operation.id), **operation.result}}))
        session.commit(); session.refresh(operation)
        return _operation_view(operation)


def build_source_metadata_router(database, worker, *, mutations_enabled=False):
    router = APIRouter(prefix="/api/materials", tags=["source metadata"])

    @router.get("/{material_id}/source-metadata")
    def inspect(material_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session); material = _material(session, material_id, access)
            folder = material.folder_path; revision = material.updated_at
        source = None
        if folder:
            try: source = worker.inspect(folder)
            except MetadataClientError: pass
        with database.session() as session:
            access.check(session); material = _material(session, material_id, access)
            if material.updated_at != revision or material.folder_path != folder:
                raise HTTPException(409, "Material changed while source metadata was loading.")
            if source is not None and source.folder_name != material.technical_identity: source = None
            active = session.scalar(select(MaterialMetadataOperation).where(
                MaterialMetadataOperation.material_id == material_id, MaterialMetadataOperation.status == "RUNNING"))
            return {"available": source is not None, "writes_enabled": bool(mutations_enabled and source and source.writes_enabled),
                    "editable": bool(source and source.editable), "expected_updated_at": material.updated_at,
                    "sha256": source.sha256 if source else None, "source_status": source.status if source else "UNAVAILABLE",
                    "values": _values(source if source and source.status != "MISSING" else session.get(PBRMaterialMetadata, material_id)),
                    "active_operation": _operation_view(active) if active else None}

    @router.post("/{material_id}/source-metadata")
    def save(material_id: UUID, payload: MetadataSaveRequest, access: AccessDependency):
        request_hash = canonical_hash({"material_id": str(material_id), "payload": payload.model_dump(mode="json")})
        with database.session() as session:
            actor = access.check(session, MATERIAL_EDITORS); material = _material(session, material_id, access, lock=True)
            existing = session.scalar(select(MaterialMetadataOperation).where(
                MaterialMetadataOperation.actor_id == actor.id, MaterialMetadataOperation.request_key == payload.idempotency_key))
            if existing:
                if existing.material_id != material_id or existing.request_hash != request_hash:
                    raise HTTPException(409, "Idempotency key belongs to a different metadata edit.")
                return _operation_view(existing)
            if not mutations_enabled: raise HTTPException(503, {"code": "SOURCE_MUTATIONS_DISABLED"})
            if _aware(material.updated_at) != _aware(payload.expected_updated_at): raise HTTPException(409, "Material changed; reload before saving metadata.")
            if not material.folder_path: raise HTTPException(409, "Link the source folder before editing metadata.")
            if material.folder_path.rsplit("/", 1)[-1] != material.technical_identity:
                raise HTTPException(409, "Linked source folder does not match the material identity.")
            folder = material.folder_path
            require_material_idle(session, material.id)
        observation = None
        try: observation = worker.inspect(folder)
        except MetadataClientError: pass
        # IO can outlive reassignment, access revocation or a completed duplicate
        # request. Reauthorize and replay before reserving durable source ownership.
        with database.session() as session:
            actor = access.check(session, MATERIAL_EDITORS); material = _material(session, material_id, access, lock=True)
            existing = session.scalar(select(MaterialMetadataOperation).where(
                MaterialMetadataOperation.actor_id == actor.id, MaterialMetadataOperation.request_key == payload.idempotency_key))
            if existing:
                if existing.material_id != material_id or existing.request_hash != request_hash:
                    raise HTTPException(409, "Idempotency key belongs to a different metadata edit.")
                return _operation_view(existing)
            if _aware(material.updated_at) != _aware(payload.expected_updated_at) or material.folder_path != folder:
                raise HTTPException(409, "Material changed while source metadata was inspected.")
            if observation is None: raise HTTPException(503, {"code": "SOURCE_METADATA_UNAVAILABLE"})
            if not observation.writes_enabled: raise HTTPException(503, {"code": "SOURCE_MUTATIONS_DISABLED"})
            if not observation.editable: raise HTTPException(422, {"code": "SOURCE_METADATA_UNSUPPORTED"})
            if observation.folder_name != material.technical_identity or observation.sha256 != payload.expected_sha256:
                raise HTTPException(409, {"code": "METADATA_SOURCE_CHANGED"})
            lock_folder_catalog(session); require_material_idle(session, material.id); require_folder_idle(session, material.folder_path)
            state = _state(session, material.id, create=True)
            operation = MaterialMetadataOperation(id=uuid4(), material_id=material.id, actor_id=actor.id,
                brand_id=material.published_brand_id, folder_path=material.folder_path, request_key=payload.idempotency_key,
                request_hash=request_hash, request_payload=payload.model_dump(mode="json"), status="RUNNING")
            session.add(operation)
            invalidate_review(session, material, actor.id, "SOURCE_METADATA_STARTED", record_event=False)
            session.add(MaterialAuditEvent(material_id=material.id, actor_id=actor.id,
                event_type="SOURCE_METADATA_STARTED", generation=state.generation, revision_hash=None,
                result={"audit": {"operation_id": str(operation.id), "expected_sha256": payload.expected_sha256,
                                   "values": payload.values.model_dump()}}))
            try: session.commit()
            except IntegrityError:
                session.rollback(); raise HTTPException(409, "Concurrent metadata edit conflicted with this request.") from None
        return _finish(database, operation.id, worker)

    @router.post("/{material_id}/source-metadata/{operation_id}/resume")
    def resume(material_id: UUID, operation_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session, MATERIAL_EDITORS); _material(session, material_id, access)
            operation = session.get(MaterialMetadataOperation, operation_id)
            if operation is None or operation.material_id != material_id: raise HTTPException(404, "Metadata operation not found.")
            if operation.status != "RUNNING": return _operation_view(operation)
        if not mutations_enabled: raise HTTPException(503, {"code": "SOURCE_MUTATIONS_DISABLED"})
        return _finish(database, operation_id, worker)

    return router
