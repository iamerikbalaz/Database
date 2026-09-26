"""Publication drafts and exact, derived human-approval status. No source IO."""
from datetime import UTC

from sqlalchemy import select

from app.db.models import (OnlineCategory, BrandCollection, MaterialContent, MaterialOnlineCategory,
    MaterialCollection, MaterialContentApproval, MaterialContentRevision, MaterialReviewState, PublishedBrand)
from app.material_review import canonical_hash, material_context


def catalog_view(item, *, details=False):
    result = {"id": str(item.id), "value": item.value, "version": item.version, "is_active": item.is_active}
    if isinstance(item, BrandCollection):
        result["brand_id"] = str(item.brand_id)
    # Administrative metadata must not change immutable content snapshots or
    # invalidate existing approval hashes merely because a column was added.
    if details:
        created = item.created_at if item.created_at.tzinfo else item.created_at.replace(tzinfo=UTC)
        result.update(abbreviation=item.abbreviation, created_at=created.isoformat())
    return result


def draft_view(session, material):
    content = session.get(MaterialContent, material.id)
    categories = list(session.scalars(select(OnlineCategory).join(MaterialOnlineCategory)
        .where(MaterialOnlineCategory.material_id == material.id).order_by(OnlineCategory.normalized_key, OnlineCategory.id)))
    collections = list(session.scalars(select(BrandCollection).join(MaterialCollection)
        .where(MaterialCollection.material_id == material.id).order_by(BrandCollection.normalized_key, BrandCollection.id)))
    result = {"material_id": str(material.id), "revision": content.revision if content else 0,
        "description": content.description if content else None, "credits": content.credits if content else None,
        "tags": content.tags if content else [], "categories": [catalog_view(item) for item in categories],
        "collections": [catalog_view(item) for item in collections],
        "content_status": "MANUAL_DRAFT" if content else "EMPTY"}
    if content:
        saved = session.scalar(select(MaterialContentRevision.snapshot).where(MaterialContentRevision.material_id == material.id,
            MaterialContentRevision.revision == content.revision))
        if saved and saved.get("ai_provenance"):
            result["ai_provenance"] = saved["ai_provenance"]
            result["content_status"] = saved["content_status"]
    return result


def approval_view(item):
    created = item.created_at if item.created_at.tzinfo else item.created_at.replace(tzinfo=UTC)
    return {"id": str(item.id), "actor_id": str(item.actor_id), "content_revision": item.content_revision,
        "context_hash": item.context_hash, "note": item.note, "warnings_acknowledged": item.warnings_acknowledged,
        "created_at": created.isoformat()}


def content_review(session, material, *, draft=None):
    # Caller holds a material lock. The shared brand lock prevents approval from
    # racing a brand update; catalog writes use the exclusive domain gate.
    draft = draft if draft is not None else draft_view(session, material)
    brand = session.scalar(select(PublishedBrand).where(PublishedBrand.id == material.published_brand_id).with_for_update(read=True))
    state = session.get(MaterialReviewState, material.id)
    snapshot = {"schema_version": 1, "content": draft, "material": material_context(material),
        "brand": {"id": str(brand.id), "company_id": str(brand.company_id), "name": brand.name,
                  "brand_identifier": brand.brand_identifier, "folder_prefix": brand.folder_prefix, "is_active": brand.is_active},
        "source_review": {"generation": state.generation if state else 0,
                          "revision_hash": state.revision_hash if state else None}}
    context_hash = canonical_hash(snapshot)
    errors = []
    warnings = []
    if not draft["revision"]: errors.append("CONTENT_DRAFT_REQUIRED")
    if draft["credits"] is None: errors.append("CONTENT_CREDITS_REQUIRED")
    if not draft["categories"]: errors.append("CONTENT_CATEGORIES_REQUIRED")
    if not brand.is_active: errors.append("CONTENT_BRAND_INACTIVE")
    if any(not item["is_active"] for item in draft["categories"] + draft["collections"]):
        errors.append("CONTENT_CATALOG_VALUE_INACTIVE")
    if any(item["brand_id"] != str(brand.id) for item in draft["collections"]):
        errors.append("CONTENT_COLLECTION_BRAND_MISMATCH")
    if not draft["description"]: warnings.append("CONTENT_DESCRIPTION_EMPTY")
    if not draft["tags"]: warnings.append("CONTENT_TAGS_EMPTY")
    approval = session.scalar(select(MaterialContentApproval).where(MaterialContentApproval.material_id == material.id,
        MaterialContentApproval.context_hash == context_hash)) if not errors else None
    return {"material_id": str(material.id), "content_revision": draft["revision"], "context_hash": context_hash,
        "snapshot": snapshot, "errors": errors, "warnings": warnings,
        "can_approve": not errors and approval is None,
        "content_status": "APPROVED" if approval else draft["content_status"],
        "approval": approval_view(approval) if approval else None}


def content_view(session, material):
    draft = draft_view(session, material)
    review = content_review(session, material, draft=draft)
    return {**draft, "content_status": review["content_status"]}
