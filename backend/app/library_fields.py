"""Small revision-checked content edits used by material batch preparation."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field

from app.api.material_review import _material, _record, _replay, _request_hash, _state
from app.auth.access import MATERIAL_EDITORS
from app.auth.service import _aware
from app.catalog import ContentUpdate, TagValue, value_key
from app.content_saves import apply_material_content
from app.material_identity import require_material_idle
from app.publication_content import content_view
from app.schemas import ApiSchema


class LibraryFieldBase(ApiSchema):
    idempotency_key: UUID
    expected_updated_at: datetime
    expected_revision: Annotated[int, Field(strict=True, ge=0)]


class LibraryTagsAppend(LibraryFieldBase):
    field: Literal["tags"]
    tags: Annotated[list[TagValue], Field(min_length=1, max_length=100)]


class LibraryCreditsChange(LibraryFieldBase):
    field: Literal["credits"]
    credits: Annotated[int, Field(strict=True, ge=0, le=2147483647)] | None


LibraryFieldChange = Annotated[LibraryTagsAppend | LibraryCreditsChange, Field(discriminator="field")]


def save_library_field(database, material_id, payload, access):
    request_hash = _request_hash("LIBRARY_FIELD_CHANGED", material_id, payload)
    with database.session() as session:
        actor = access.check(session, MATERIAL_EDITORS)
        # A lost success response remains recoverable after another operation
        # archives or removes the record. The immutable receipt belongs to this
        # authenticated actor and exact command; new writes still require normal
        # active-material authorization below.
        replay = _replay(session, actor.id, material_id, payload, request_hash)
        if replay is not None:
            return replay
        material = _material(session, material_id, access, lock=True)
        # Serialize duplicate submissions before deciding a new write is needed.
        replay = _replay(session, actor.id, material_id, payload, request_hash)
        if replay is not None:
            return replay
        require_material_idle(session, material.id)
        if _aware(material.updated_at) != _aware(payload.expected_updated_at):
            raise HTTPException(409, {"code": "MATERIAL_CHANGED"})
        before = content_view(session, material)
        if before["revision"] != payload.expected_revision:
            raise HTTPException(409, {"code": "CONTENT_REVISION_CHANGED"})
        tags = before["tags"]
        if payload.field == "tags":
            unique = {value_key(value): value for value in tags}
            for value in payload.tags:
                unique.setdefault(value_key(value), value)
            tags = list(unique.values())
            if len(tags) > 100:
                raise HTTPException(422, {"code": "TOO_MANY_TAGS"})
        change = ContentUpdate(idempotency_key=payload.idempotency_key,
            expected_revision=payload.expected_revision, description=before["description"],
            credits=payload.credits if payload.field == "credits" else before["credits"], tags=tags,
            category_ids=[item["id"] for item in before["categories"]],
            collection_ids=[item["id"] for item in before["collections"]],
            reason="Bulk library field updated: " + payload.field)
        body = apply_material_content(session, material, change, actor.id)
        return _record(session, material, _state(session, material.id, create=True), actor.id,
            "LIBRARY_FIELD_CHANGED", payload, request_hash, body,
            audit={"field": payload.field, "revision": body["revision"]})
