"""Revision invalidation shared by material mutations. Caller holds material lock."""
import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.orm import Session

from app.db.models import MaterialAuditEvent, MaterialReviewState, PBRMaterial


def canonical_hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def material_context(material: PBRMaterial) -> dict:
    return {"material_id": str(material.id), "technical_identity": material.technical_identity,
            "folder_path": material.folder_path, "material_name": material.material_name,
            "main_category_code": material.main_category_code, "project_id": str(material.project_id) if material.project_id is not None else None,
            "published_brand_id": str(material.published_brand_id), "assigned_processor_id": str(material.assigned_processor_id)}


class ReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    generation: int = 0
    revision_hash: str | None = None
    inventory_id: UUID | None = None
    checked_at: datetime | None = None
    failure_code: str | None = None

    @field_validator("checked_at")
    @classmethod
    def utc_timestamp(cls, value):
        return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def read_review(state: MaterialReviewState | None) -> dict:
    return (ReviewRead.model_validate(state) if state is not None else ReviewRead()).model_dump(mode="json")


def invalidate_review(session: Session, material: PBRMaterial, actor_id: UUID,
                      reason: str, *, record_event: bool = True) -> MaterialReviewState | None:
    state = session.get(MaterialReviewState, material.id)
    if state is None:
        return None  # No inventory or approval exists yet to invalidate.
    state.generation += 1
    state.revision_hash = None
    state.inventory_id = None
    state.technical_check_id = None
    state.failure_code = reason
    material.validation_status = "NOT_CHECKED"
    if material.is_published:
        material.publication_status = "PUBLISHED_UPDATE_REQUIRED"
    if record_event:
        session.add(MaterialAuditEvent(material_id=material.id, actor_id=actor_id,
            event_type=reason, generation=state.generation, revision_hash=None, result={"audit": {"reason": reason}}))
    return state
