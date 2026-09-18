"""Explicit local archive/restore, with immutable exact-command recovery."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.access import ADMIN, AccessDependency
from app.auth.service import _aware, database_now
from app.db.models import (MaterialLifecycleEvent, MaterialLifecycleState, MaterialReviewState,
    PBRMaterial, PBRMaterialMetadata, PublicationStagingDispatch, PublicationStagingItem)
from app.material_identity import lock_folder_catalog, require_folder_idle, require_material_idle
from app.material_review import canonical_hash, invalidate_review
from app.resource_history import resource_snapshot
from app.schemas import ApiSchema, Sha256


class LifecycleCommand(ApiSchema):
    action: Literal["ARCHIVE", "RESTORE"]
    request_key: UUID
    expected_version: int = Field(ge=0, le=2147483646, strict=True)
    expected_input_sha256: Sha256
    reason: str = Field(min_length=1, max_length=2000, strict=True)
    acknowledge: bool = Field(strict=True)

    @field_validator("request_key")
    @classmethod
    def nonzero_key(cls, value):
        if not value.int: raise ValueError("A nonzero request key is required")
        return value

    @field_validator("reason")
    @classmethod
    def readable_reason(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 and char not in "\n\r\t" or ord(char) == 127 for char in value):
            raise ValueError("A readable reason is required")
        return value

    @field_validator("acknowledge")
    @classmethod
    def confirmed(cls, value):
        if not value: raise ValueError("Explicit acknowledgment is required")
        return value


def _target(session, identifier, *, lock=False):
    query = select(PBRMaterial).where(PBRMaterial.id == identifier).execution_options(populate_existing=True)
    material = session.scalar(query.with_for_update() if lock else query)
    if material is None: raise HTTPException(404, {"code": "MATERIAL_NOT_FOUND"})
    return material


def _version(material):
    return material.lifecycle_state.version if material.lifecycle_state else 0


def _detail(material):
    lifecycle = material.lifecycle_state
    return {"material": resource_snapshot(material), "is_archived": material.is_archived,
        "version": _version(material), "changed_at": _aware(lifecycle.changed_at).isoformat() if lifecycle else None}


def _input_hash(session, material):
    review = session.get(MaterialReviewState, material.id)
    metadata = session.get(PBRMaterialMetadata, material.id)
    return canonical_hash({**_detail(material), "updated_at": _aware(material.updated_at).isoformat(),
        "review_generation": review.generation if review else 0,
        "metadata_snapshot_id": str(metadata.current_snapshot_id) if metadata and metadata.current_snapshot_id else None,
        "metadata_status": metadata.status if metadata else None})


def _eligible(session, material, action):
    if material.is_archived != (action == "RESTORE"):
        raise HTTPException(409, {"code": "MATERIAL_LIFECYCLE_STATE_CHANGED"})
    if material.is_published or material.publication_status != "NOT_PUBLISHED":
        raise HTTPException(409, {"code": "MATERIAL_LIFECYCLE_PUBLICATION_BLOCKED"})
    review = session.get(MaterialReviewState, material.id)
    if _version(material) >= 2147483647 or review and review.generation >= 9223372036854775807:
        raise HTTPException(409, {"code": "MATERIAL_LIFECYCLE_VERSION_EXHAUSTED"})
    require_material_idle(session, material.id)
    # Closing an attempted upload releases local ownership, not external state.
    if session.scalar(select(PublicationStagingDispatch.id).join(PublicationStagingItem,
            PublicationStagingItem.job_id == PublicationStagingDispatch.job_id).where(
            PublicationStagingItem.material_id == material.id).limit(1)):
        raise HTTPException(409, {"code": "MATERIAL_LIFECYCLE_EXTERNAL_STATE_BLOCKED"})
    lock_folder_catalog(session)
    if material.folder_path: require_folder_idle(session, material.folder_path)
    if session.get(PBRMaterialMetadata, material.id) is None:
        raise HTTPException(409, {"code": "MATERIAL_METADATA_STATE_MISSING"})


def event_view(event):
    return {"id": str(event.id), "material_id": str(event.material_id), "actor_id": str(event.actor_id),
        "version": event.version, "action": event.action, "request_key": str(event.request_key),
        "request_sha256": event.request_hash, "input_sha256": event.input_hash, "reason": event.reason,
        "review_generation": event.review_generation, "created_at": _aware(event.created_at).isoformat()}


def _replay(session, actor_id, material_id, payload, digest):
    event = session.scalar(select(MaterialLifecycleEvent).where(
        MaterialLifecycleEvent.actor_id == actor_id, MaterialLifecycleEvent.request_key == payload.request_key))
    if event is None: return None
    if event.material_id != material_id or event.request_hash != digest:
        raise HTTPException(409, {"code": "MATERIAL_LIFECYCLE_REQUEST_KEY_REUSED"})
    return {"event": event_view(event)}


def build_material_archives_router(database):
    router = APIRouter(prefix="/api/material-archives", tags=["material lifecycle"])

    @router.get("")
    def listing(access: AccessDependency, after: UUID | None = None):
        with database.session() as session:
            access.check(session, ADMIN)
            query = select(PBRMaterial).join(MaterialLifecycleState).where(MaterialLifecycleState.is_archived.is_(True))
            if after is not None: query = query.where(PBRMaterial.id > after)
            materials = list(session.scalars(query.order_by(PBRMaterial.id).limit(21)))
            return {"items": [_detail(material) for material in materials[:20]],
                "next_cursor": str(materials[19].id) if len(materials) > 20 else None}

    @router.get("/{material_id}")
    def detail(material_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session, ADMIN)
            return _detail(_target(session, material_id))

    @router.get("/{material_id}/preview")
    def preview(material_id: UUID, action: Literal["ARCHIVE", "RESTORE"], access: AccessDependency):
        with database.session() as session:
            access.check(session, ADMIN)
            material = _target(session, material_id, lock=True)
            blocked = None
            try: _eligible(session, material, action)
            except HTTPException as error:
                if error.status_code != 409: raise
                blocked = error.detail["code"]
            return {**_detail(material), "action": action, "input_sha256": _input_hash(session, material),
                "can_apply": blocked is None, "blocked_code": blocked}

    @router.get("/{material_id}/history")
    def history(material_id: UUID, access: AccessDependency, after: UUID | None = None):
        with database.session() as session:
            access.check(session, ADMIN)
            _target(session, material_id)
            query = select(MaterialLifecycleEvent).where(MaterialLifecycleEvent.material_id == material_id)
            if after is not None:
                cursor = session.scalar(select(MaterialLifecycleEvent).where(MaterialLifecycleEvent.id == after,
                    MaterialLifecycleEvent.material_id == material_id))
                if cursor is None: raise HTTPException(409, {"code": "MATERIAL_LIFECYCLE_CURSOR_INVALID"})
                query = query.where(MaterialLifecycleEvent.version < cursor.version)
            events = list(session.scalars(query.order_by(MaterialLifecycleEvent.version.desc()).limit(21)))
            return {"material_id": str(material_id), "items": [event_view(event) for event in events[:20]],
                "next_cursor": str(events[19].id) if len(events) > 20 else None}

    @router.get("/{material_id}/commands/{request_key}")
    def recover(material_id: UUID, request_key: UUID, access: AccessDependency):
        with database.session() as session:
            actor = access.check(session, ADMIN)
            event = session.scalar(select(MaterialLifecycleEvent).where(MaterialLifecycleEvent.material_id == material_id,
                MaterialLifecycleEvent.actor_id == actor.id, MaterialLifecycleEvent.request_key == request_key))
            if event is None: raise HTTPException(404, {"code": "MATERIAL_LIFECYCLE_COMMAND_NOT_FOUND"})
            return {"event": event_view(event)}

    @router.post("/{material_id}/commands")
    def command(material_id: UUID, payload: LifecycleCommand, access: AccessDependency):
        digest = canonical_hash({"operation": "MATERIAL_LIFECYCLE", "material_id": str(material_id),
            "request": payload.model_dump(mode="json")})
        with database.session() as session:
            actor = access.check(session, ADMIN)
            replay = _replay(session, actor.id, material_id, payload, digest)
            if replay is not None: return replay
            material = _target(session, material_id, lock=True)
            replay = _replay(session, actor.id, material_id, payload, digest)
            if replay is not None: return replay
            if _version(material) != payload.expected_version or _input_hash(session, material) != payload.expected_input_sha256:
                raise HTTPException(409, {"code": "MATERIAL_LIFECYCLE_INPUT_CHANGED"})
            _eligible(session, material, payload.action)
            review = session.get(MaterialReviewState, material_id)
            if review is None:
                review = MaterialReviewState(material_id=material_id, generation=0)
                session.add(review); session.flush()
            invalidate_review(session, material, actor.id, "MATERIAL_" + payload.action, record_event=False)
            review.checked_at = None
            material.workflow_status = "IN_PROGRESS"
            material.validation_status = "NOT_CHECKED"
            metadata = session.get(PBRMaterialMetadata, material_id)
            for field in ("current_snapshot_id", "source_filename", "source_sha256", "source_content", "hex_color", "width_cm", "height_cm", "master_resolution", "loaded_at"):
                setattr(metadata, field, None)
            metadata.status = "NOT_SCANNED"; metadata.warnings = []
            now = max(database_now(session), _aware(material.updated_at))
            lifecycle = material.lifecycle_state
            if lifecycle:
                now = max(now, _aware(lifecycle.changed_at))
                lifecycle.version += 1
                lifecycle.is_archived = payload.action == "ARCHIVE"
                lifecycle.changed_at = now
            else:
                lifecycle = MaterialLifecycleState(material_id=material_id, version=1, is_archived=True, changed_at=now)
                material.lifecycle_state = lifecycle
                session.add(lifecycle)
            material.updated_at = now
            session.flush()
            event = MaterialLifecycleEvent(material_id=material_id, actor_id=actor.id, version=lifecycle.version,
                action=payload.action, request_key=payload.request_key, request_hash=digest,
                input_hash=payload.expected_input_sha256, reason=payload.reason, review_generation=review.generation, created_at=now)
            session.add(event)
            try:
                session.flush(); result = {"event": event_view(event)}
                session.commit()
            except IntegrityError:
                session.rollback()
                raise HTTPException(409, {"code": "MATERIAL_LIFECYCLE_CONFLICT"}) from None
            return result

    return router
