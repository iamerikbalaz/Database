"""Add categories and collections to an explicit selection, atomically."""
from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid5

from fastapi import APIRouter, HTTPException
from pydantic import Field, model_validator
from sqlalchemy import select

from app.api.material_review import _material, _state
from app.auth.access import AccessDependency, MATERIAL_EDITORS
from app.catalog import ContentUpdate
from app.content_saves import apply_material_content
from app.db.material_creation_models import MaterialContentBatch
from app.db.models import MaterialAuditEvent
from app.material_identity import require_material_idle
from app.material_review import canonical_hash
from app.material_table import utc
from app.publication_content import content_view
from app.schemas import ApiSchema


class ContentSelection(ApiSchema):
    id: UUID
    expected_updated_at: datetime
    expected_revision: Annotated[int, Field(strict=True, ge=0)]


class BulkContentAdd(ApiSchema):
    idempotency_key: UUID
    materials: list[ContentSelection] = Field(min_length=1, max_length=100)
    category_ids: list[UUID] = Field(default_factory=list, max_length=100)
    collection_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_selection(self):
        if self.idempotency_key.int == 0 or len({item.id for item in self.materials}) != len(self.materials):
            raise ValueError("Use a nonzero key and distinct materials.")
        if not self.category_ids and not self.collection_ids:
            raise ValueError("Select at least one category or collection to add.")
        return self


def build_material_bulk_content_router(database):
    router = APIRouter(prefix="/api", tags=["material content"])

    @router.post("/material-content-batches")
    def add_content(payload: BulkContentAdd, access: AccessDependency):
        request_hash = canonical_hash(payload.model_dump(mode="json"))
        with database.session() as session:
            actor = access.check(session, MATERIAL_EDITORS)
            materials = {item.id: _material(session, item.id, access, lock=True)
                         for item in sorted(payload.materials, key=lambda item: str(item.id))}
            receipt = session.scalar(select(MaterialContentBatch).where(
                MaterialContentBatch.actor_id == actor.id, MaterialContentBatch.request_key == payload.idempotency_key))
            if receipt:
                if receipt.request_hash != request_hash:
                    raise HTTPException(409, {"code": "CONTENT_BATCH_KEY_REUSED"})
                return receipt.response_snapshot
            if payload.collection_ids and len({item.published_brand_id for item in materials.values()}) != 1:
                raise HTTPException(409, {"code": "CONTENT_BATCH_CUSTOMER_MISMATCH",
                    "message": "Brand collections can only be added to materials of one Customer."})
            changes = []
            for selected in payload.materials:
                material = materials[selected.id]
                require_material_idle(session, material.id)
                before = content_view(session, material)
                if utc(material.updated_at) != utc(selected.expected_updated_at) or before["revision"] != selected.expected_revision:
                    raise HTTPException(409, {"code": "CONTENT_BATCH_CHANGED", "message": "A selected material changed. Reload the selection."})
                categories = list(set(payload.category_ids) | {UUID(item["id"]) for item in before["categories"]})
                collections = list(set(payload.collection_ids) | {UUID(item["id"]) for item in before["collections"]})
                if len(categories) > 100 or len(collections) > 100:
                    raise HTTPException(409, {"code": "CONTENT_BATCH_LIMIT", "message": "The resulting selection exceeds 100 categories or collections."})
                change = ContentUpdate(idempotency_key=uuid5(payload.idempotency_key, str(material.id)),
                    expected_revision=before["revision"], description=before["description"], credits=before["credits"], tags=before["tags"],
                    category_ids=categories, collection_ids=collections,
                    reason="Categories and collections added to selected materials")
                changes.append((material, change))
            results = []
            for material, change in changes:
                body = apply_material_content(session, material, change, actor.id)
                state = _state(session, material.id, create=True)
                session.add(MaterialAuditEvent(material_id=material.id, actor_id=actor.id, event_type="CONTENT_SAVED",
                    generation=state.generation, revision_hash=state.revision_hash,
                    request_key=change.idempotency_key, request_hash=request_hash,
                    result={"status_code": 200, "body": body, "audit": {"revision": body["revision"], "reason": change.reason}}))
                results.append({"material_id": str(material.id), "revision": body["revision"]})
            response = {"items": results, "updated_count": len(results)}
            session.add(MaterialContentBatch(actor_id=actor.id, request_key=payload.idempotency_key,
                request_hash=request_hash, response_snapshot=response))
            session.commit()
            return response

    return router
