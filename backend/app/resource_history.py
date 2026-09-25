"""Whitelisted audit of ordinary resource create/PATCH transactions.

Specialized workflow and credential events keep their separate existing histories.
Caller holds the access gate and target lock. This helper never commits.
"""
from datetime import date
from uuid import UUID

from sqlalchemy import func, select

from app.db.models import InternalUser, PBRMaterial, Project, PublishedBrand, ResourceChangeEvent
from app.material_review import canonical_hash

KINDS = {
    "BRAND": (PublishedBrand, "brand_id", "brands", ("company_id", "name", "folder_prefix", "brand_identifier", "is_active")),
    "PROJECT": (Project, "project_id", "projects", ("company_id", "project_number", "name", "status", "due_date", "notes")),
    "USER": (InternalUser, "user_id", "internal-users", ("display_name", "email", "role", "is_active")),
    "MATERIAL": (PBRMaterial, "material_id", "materials", ("project_id", "published_brand_id", "sequence_number", "material_name",
        "main_category_code", "assigned_processor_id", "technical_identity", "folder_path", "workflow_status", "validation_status", "is_published", "publication_status", "checked_status", "note")),
}


def resource_kind(item):
    return next(kind for kind, (model, *_) in KINDS.items() if isinstance(item, model))


def resource_snapshot(item):
    def primitive(value):
        if isinstance(value, UUID): return str(value)
        if isinstance(value, date): return value.isoformat()
        if value is None or isinstance(value, (str, bool, int)): return value
        raise ValueError("Unsupported resource history value")
    return {field: primitive(getattr(item, field)) for field in ("id", *KINDS[resource_kind(item)][3])}


def valid_snapshot(value, kind, identifier):
    fields = {"id", *KINDS[kind][3]}
    optional = {"checked_status", "note"} if kind == "MATERIAL" else set()
    return (isinstance(value, dict) and fields - optional <= set(value) <= fields
        and value["id"] == str(identifier) and all(item is None or isinstance(item, (str, int, bool)) for item in value.values()))


def append_resource_change(session, item, actor_id, before, *, action="UPDATED"):
    session.flush()
    after = resource_snapshot(item); kind = resource_kind(item)
    if before == after: return None
    if action not in {"CREATED", "UPDATED"} or not isinstance(actor_id, UUID):
        raise ValueError("Invalid resource history context")
    if (action == "CREATED" and before != {}) or (action == "UPDATED" and not valid_snapshot(before, kind, item.id)):
        raise ValueError("Invalid resource history snapshot")
    target = KINDS[kind][1]
    version = (session.scalar(select(func.max(ResourceChangeEvent.version)).where(getattr(ResourceChangeEvent, target) == item.id)) or 0) + 1
    event = ResourceChangeEvent(kind=kind, **{target: item.id}, actor_id=actor_id, version=version, action=action,
        before_snapshot=before, after_snapshot=after, before_hash=canonical_hash(before), after_hash=canonical_hash(after))
    session.add(event); session.flush()
    return event
