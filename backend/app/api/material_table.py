"""Optimistic, receipted cell writes; bulk clients freeze IDs and versions."""
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.api.material_operations import (_revision, _reauthorized_preflight,
    _require_matching_identity, _require_safe_preflight, _metadata_values)
from app.api.resources import _commit_resource, _get_or_404, _require_active_internal_user, _technical_identity
from app.auth.access import AccessDependency, MATERIAL_EDITORS
from app.db.models import (PBRMaterial, PBRMaterialMetadata, PBRMaterialMetadataSnapshot, Project,
    PublishedBrand, MaterialNumberReservation, MaterialIdentityHistory)
from app.material_identity import require_material_idle, require_brand_idle, identity_context
from app.material_review import invalidate_review
from app.material_table import MaterialTableUpdate, utc
from app.resource_commands import CommandInput, CommandKey, ResourceWrite
from app.resource_history import resource_snapshot
from app.schemas import PBRMaterialRead


def build_material_table_router(database, worker_client):
    router = APIRouter(prefix="/api/materials", tags=["material table"])

    @router.patch("/{material_id}/table", response_model=PBRMaterialRead)
    def update_cell(material_id: UUID, payload: MaterialTableUpdate, access: AccessDependency,
                    submitted: CommandInput, request_key: CommandKey = None):
        if request_key is None:
            raise HTTPException(422, "Idempotency-Key is required for table edits.")
        values = payload.model_dump(exclude_unset=True, exclude={"expected_updated_at"})
        field, value = next(iter(values.items()))
        command = ResourceWrite(access, "MATERIAL", "UPDATED", payload, request_key,
                                material_id, raw_payload=submitted)

        def current(session):
            actor = access.check(session, MATERIAL_EDITORS)
            if access.user.role == "PROCESSOR" and field not in {"note", "workflow_status"}:
                raise HTTPException(403, "Only a production lead or administrator can change this property.")
            if (replay := command.replay(session)) is not None:
                return actor, None, replay
            material = session.scalar(select(PBRMaterial).where(PBRMaterial.id == material_id).with_for_update())
            if material is None:
                raise HTTPException(404, "PBR material not found.")
            access.require_material(material)
            require_material_idle(session, material_id)
            if utc(material.updated_at) != utc(payload.expected_updated_at):
                raise HTTPException(409, "This material changed. Reload it before editing again.")
            return actor, material, None

        # Do not hold a database/authorization lock across worker IO.
        with database.session() as session:
            _, material, replay = current(session)
            if replay is not None:
                return replay
            expected = _revision(material)
            completing = field == "workflow_status" and value == "DONE" and material.workflow_status != "DONE"
            if completing and material.folder_path is None:
                raise HTTPException(409, "Link a source folder before marking this material Done.")
        preflight = None
        if completing:
            try:
                preflight = _reauthorized_preflight(database, material_id, access, expected, worker_client, expected.folder_path)
            except HTTPException as exc:
                if exc.status_code == 503:
                    raise HTTPException(503, {"code": "TABLE_PREFLIGHT_UNAVAILABLE"}) from None
                raise
            _require_matching_identity(preflight, expected.technical_identity)
            _require_safe_preflight(preflight, expected.technical_identity)

        with database.session() as session:
            actor, material, replay = current(session)
            if replay is not None:
                return replay
            before = resource_snapshot(material)
            if field == "project_id" and value is not None:
                _get_or_404(session, Project, value, "Project")
            if field == "assigned_processor_id":
                _require_active_internal_user(session, value)
            if field == "checked_status" and value == "OK" and material.workflow_status != "DONE":
                raise HTTPException(409, "Mark the material Done before checking it OK.")
            changed = getattr(material, field) != value
            if changed and field in {"main_category_code", "published_brand_id"}:
                if material.folder_path is not None:
                    raise HTTPException(409, "Use a controlled identity plan for a linked folder.")
                if material.workflow_status != "IN_PROGRESS" or material.is_published:
                    raise HTTPException(409, "Identity changes require an unpublished material in progress.")
                old_context = identity_context(material)
                brand = session.scalar(select(PublishedBrand).where(PublishedBrand.id == (value if field == "published_brand_id" else material.published_brand_id)).with_for_update())
                if brand is None or not brand.is_active:
                    raise HTTPException(409, "Choose an active published brand.")
                require_brand_idle(session, brand.id)
                if field == "published_brand_id":
                    from app.db.models import MaterialCollection
                    if session.scalar(select(MaterialCollection.material_id).where(MaterialCollection.material_id == material_id).limit(1)):
                        raise HTTPException(409, "Remove brand collections before transferring this material.")
                    if brand.next_sequence_number > 9999:
                        raise HTTPException(409, "The target brand sequence is exhausted.")
                    material.sequence_number = brand.next_sequence_number
                    brand.next_sequence_number += 1
                    material.published_brand_id = brand.id
                    session.add(MaterialNumberReservation(brand_id=brand.id, sequence_number=material.sequence_number,
                        material_id=material.id, actor_id=actor.id))
                else:
                    material.main_category_code = value
                material.technical_identity = _technical_identity(brand, material.sequence_number, material.main_category_code,
                    material.material_name, source_identity=old_context["technical_identity"])
                session.add(MaterialIdentityHistory(material_id=material.id, actor_id=actor.id,
                    old_context=old_context, new_context=identity_context(material), reason="Identity changed in the material list before linking a folder."))
            if changed and field in {"project_id", "assigned_processor_id", "workflow_status", "main_category_code", "published_brand_id"}:
                invalidate_review(session, material, actor.id, "MATERIAL_TABLE_CHANGED")
            if completing:
                metadata = session.get(PBRMaterialMetadata, material_id)
                if metadata is None:
                    raise HTTPException(500, "PBR material metadata state is missing.")
                number = session.scalar(select(func.max(PBRMaterialMetadataSnapshot.sequence_number))
                    .where(PBRMaterialMetadataSnapshot.material_id == material_id)) or 0
                details = _metadata_values(preflight, datetime.now(UTC))
                snapshot = PBRMaterialMetadataSnapshot(material_id=material_id, sequence_number=number + 1, **details)
                session.add(snapshot); session.flush()
                metadata.current_snapshot_id = snapshot.id
                for key, item in details.items():
                    setattr(metadata, key, item)
            if changed and field == "checked_status" and value == "Correction":
                invalidate_review(session, material, actor.id, "MATERIAL_CORRECTION_REQUESTED")
                material.workflow_status = "IN_PROGRESS"
            setattr(material, field, value)
            if changed:
                # Explicit microseconds also make optimistic writes reliable in SQLite.
                material.updated_at = datetime.now(UTC)
            return _commit_resource(session, material, actor.id, before, command=command)

    return router
