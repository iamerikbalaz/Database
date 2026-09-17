"""Atomic publication content saves and explicit human adoption of AI proposals."""
from sqlalchemy import delete, select
from fastapi import HTTPException

from app.api.material_review import _material, _state, _record, _replay, _request_hash
from app.auth.access import MATERIAL_EDITORS
from app.catalog import ContentUpdate
from app.db.models import (OnlineCategory, BrandCollection, MaterialContent, MaterialOnlineCategory,
    MaterialCollection, MaterialContentRevision, MaterialAiDraft)
from app.material_identity import require_material_idle
from app.material_review import canonical_hash, invalidate_review
from app.publication_content import content_view, draft_view
from app.ai_content import publishing_context


def _conflict(code):
    raise HTTPException(409, {"code": code})


def _content_values(view):
    return {"description": view["description"], "credits": view["credits"], "tags": view["tags"],
        "category_ids": sorted(item["id"] for item in view["categories"]),
        "collection_ids": sorted(item["id"] for item in view["collections"])}


def save_material_content(database, material_id, payload, access, *, source_draft_id=None):
    # Ordinary saves retain their original request shape/hash, including defaults.
    # Adoption has a separate operation identity and explicit context expectation.
    operation = "CONTENT_SAVED" if source_draft_id is None else "AI_DRAFT_ADOPTED:" + str(source_draft_id)
    request_hash = _request_hash(operation, material_id, payload)
    original_request = payload
    with database.session() as session:
        actor = access.check(session, MATERIAL_EDITORS)
        material = _material(session, material_id, access, lock=True)
        replay = _replay(session, actor.id, material_id, payload, request_hash)
        if replay is not None: return replay
        require_material_idle(session, material.id)
        before = content_view(session, material)
        if before["revision"] != payload.expected_revision: _conflict("CONTENT_REVISION_CHANGED")
        provenance = before.get("ai_provenance")
        if source_draft_id is not None:
            source = session.get(MaterialAiDraft, source_draft_id)
            if source is None or source.material_id != material_id: raise HTTPException(404, "AI proposal not found.")
            current = publishing_context(session, material)
            if source.context_hash != payload.expected_context_hash or source.context_hash != current["context_hash"]:
                _conflict("AI_CONTEXT_CHANGED")
            provenance = {"draft_id": str(source.id), "provider": source.provider, "model": source.model,
                "prompt_version": source.prompt_version, "context_hash": source.context_hash,
                "edited": payload.description != source.description or payload.tags != source.tags}
            if source.service_credential_id: provenance["service_credential_id"] = str(source.service_credential_id)
            # Only prose/tags are adopted. Credit and membership values come from
            # the currently locked saved content, never from the AI proposal.
            payload = ContentUpdate(idempotency_key=payload.idempotency_key, expected_revision=payload.expected_revision,
                description=payload.description, tags=payload.tags, reason=payload.reason,
                credits=before["credits"], category_ids=[item["id"] for item in before["categories"]],
                collection_ids=[item["id"] for item in before["collections"]])
        elif provenance and (payload.description != before["description"] or payload.tags != before["tags"]):
            provenance = {**provenance, "edited": True}
        categories = list(session.scalars(select(OnlineCategory).where(OnlineCategory.id.in_(payload.category_ids))))
        collections = list(session.scalars(select(BrandCollection).where(BrandCollection.id.in_(payload.collection_ids))))
        if len(categories) != len(payload.category_ids) or len(collections) != len(payload.collection_ids): _conflict("CONTENT_CATALOG_VALUE_MISSING")
        if any(not item.is_active for item in categories + collections): _conflict("CONTENT_CATALOG_VALUE_INACTIVE")
        if any(item.brand_id != material.published_brand_id for item in collections): _conflict("CONTENT_COLLECTION_BRAND_MISMATCH")
        values = payload.model_dump(mode="json", include={"description", "credits", "tags", "category_ids", "collection_ids"})
        state = _state(session, material.id, create=True)
        if values != _content_values(before) or source_draft_id is not None:
            content = session.get(MaterialContent, material.id)
            if content is None:
                content = MaterialContent(material_id=material.id, revision=0); session.add(content)
            content.revision += 1
            content.description = payload.description; content.credits = payload.credits; content.tags = payload.tags
            for model in (MaterialOnlineCategory, MaterialCollection): session.execute(delete(model).where(model.material_id == material.id))
            session.add_all(MaterialOnlineCategory(material_id=material.id, category_id=item.id) for item in categories)
            session.add_all(MaterialCollection(material_id=material.id, collection_id=item.id) for item in collections)
            session.flush()
            body = draft_view(session, material)
            if provenance: body["ai_provenance"] = provenance
            if source_draft_id is not None: body["content_status"] = "AI_DRAFT"
            snapshot = {**body, "published_brand_id": str(material.published_brand_id), "material_name": material.material_name}
            session.add(MaterialContentRevision(material_id=material.id, revision=content.revision, actor_id=actor.id,
                snapshot=snapshot, snapshot_hash=canonical_hash(snapshot), reason=payload.reason))
            invalidate_review(session, material, actor.id, "CONTENT_CHANGED", record_event=False)
        else: body = before
        return _record(session, material, state, actor.id, "AI_DRAFT_ADOPTED" if source_draft_id else "CONTENT_SAVED", original_request, request_hash, body,
            audit={"revision": body["revision"], "reason": payload.reason, **({"ai_draft_id": str(source_draft_id)} if source_draft_id else {})})
