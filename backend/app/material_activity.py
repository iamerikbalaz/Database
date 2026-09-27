"""Read-only chronological projection over original immutable material evidence."""
from datetime import UTC
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import and_, literal, or_, select, union_all
from app.db.models import (InternalUser, ResourceChangeEvent, MaterialAuditEvent,
    MaterialContentRevision, PBRMaterialMetadataSnapshot, MaterialIdentityHistory,
    MaterialLifecycleEvent, MaterialImportBatch, MaterialImportRow, MaterialApproval,
    MaterialContentApproval, MaterialPackagingPolicy, MaterialPackagingExecution)

SOURCES = {
    "record": ResourceChangeEvent, "content": MaterialContentRevision,
    "metadata": PBRMaterialMetadataSnapshot, "identity": MaterialIdentityHistory,
    "archive": MaterialLifecycleEvent, "workflow": MaterialAuditEvent,
    "approval": MaterialApproval, "content_approval": MaterialContentApproval,
    "packaging_policy": MaterialPackagingPolicy, "packaging": MaterialPackagingExecution,
    "import": MaterialImportBatch,
}
LABELS = {"material_name": "Name", "main_category_code": "Main category", "workflow_status": "Status",
    "checked_status": "Checked", "is_published": "Published", "is_archived": "Archived", "note": "Note",
    "project_id": "Project", "published_brand_id": "Brand", "assigned_processor_id": "Processor",
    "folder_path": "Folder", "technical_identity": "Technical identity", "description": "Description",
    "credits": "Credits", "tags": "Tags", "categories": "Categories", "collections": "Collections",
    "hex_color": "Color HEX", "width_cm": "Width (cm)", "height_cm": "Height (cm)",
    "master_resolution": "Master resolution", "status": "Status"}
SAFE_FIELDS = set(LABELS) | {"sequence_number", "validation_status", "publication_status"}


def _changes(before, after):
    before, after = before or {}, after or {}
    return [{"field": field, "label": LABELS.get(field, field.replace("_", " ").capitalize()),
             "before": before.get(field), "after": after.get(field)}
            for field in sorted(SAFE_FIELDS & (set(before) | set(after))) if before.get(field) != after.get(field)]


def _metadata_values(item):
    return {field: str(getattr(item, field)) if getattr(item, field) is not None else None
        for field in ("hex_color", "width_cm", "height_cm", "master_resolution", "status")}


def _event(session, source, item, material_id):
    actor_id = getattr(item, "actor_id", None)
    action = getattr(item, "action", None) or source.upper()
    reason = getattr(item, "reason", None)
    changes = []
    summary = source.replace("_", " ").capitalize() + " updated"
    if source == "record":
        changes = _changes(item.before_snapshot, item.after_snapshot)
        summary = "Material created" if action == "CREATED" else "Properties updated"
    elif source == "content":
        previous = session.scalar(select(MaterialContentRevision.snapshot).where(
            MaterialContentRevision.material_id == material_id, MaterialContentRevision.revision == item.revision - 1))
        changes = _changes(previous, item.snapshot); summary = "Content saved"
    elif source == "metadata":
        previous = session.scalar(select(PBRMaterialMetadataSnapshot).where(PBRMaterialMetadataSnapshot.material_id == material_id,
            PBRMaterialMetadataSnapshot.sequence_number == item.sequence_number - 1))
        changes = _changes(_metadata_values(previous) if previous else {}, _metadata_values(item))
        # Old snapshots have no author column. Only an explicit audit reference
        # proves the actor; assignment or matching timestamps do not.
        audit = session.scalar(select(MaterialAuditEvent).where(MaterialAuditEvent.material_id == material_id,
            MaterialAuditEvent.result["audit"]["metadata_snapshot_id"].as_string() == str(item.id)))
        if audit is not None: actor_id = audit.actor_id
        summary = "Metadata saved"
    elif source == "identity":
        changes = _changes(item.old_context, item.new_context); summary = "Identity or folder changed"
    elif source == "archive":
        changes = _changes({"is_archived": action == "RESTORE"}, {"is_archived": action == "ARCHIVE"})
        summary = "Material archived" if action == "ARCHIVE" else "Material restored"
    elif source == "workflow":
        action = item.event_type; summary = item.event_type.replace("_", " ").capitalize()
        audit = item.result.get("audit", {})
        reason = audit.get("reason") if isinstance(audit.get("reason"), str) else None
        if isinstance(audit.get("values"), dict): changes = _changes({}, audit["values"])
    elif source == "import":
        row = session.scalar(select(MaterialImportRow).where(MaterialImportRow.material_id == material_id, MaterialImportRow.batch_id == item.id))
        changes = _changes({}, {**row.snapshot, **(row.snapshot.get("properties") or {})}); summary = "Material imported"
    elif source in {"approval", "content_approval"}:
        action = getattr(item, "kind", "CONTENT") + "_APPROVED"; summary = action.replace("_", " ").capitalize()
        reason = item.note
    elif source == "packaging": summary = "Packaging reserved"
    elif source == "packaging_policy": summary = "ZIP policy recorded"
    author = session.get(InternalUser, actor_id) if actor_id is not None else None
    created = item.created_at if item.created_at.tzinfo else item.created_at.replace(tzinfo=UTC)
    return {"id": source + ":" + str(item.id), "source": source, "action": action,
        "created_at": created.isoformat(), "author": {"id": str(author.id), "display_name": author.display_name} if author else None,
        "summary": summary, "changes": changes, "reason": reason}


def material_activity(session, material_id, *, after=None, limit=30):
    branches = []
    for source, model in SOURCES.items():
        query = select(literal(source).label("source"), model.id.label("id"), model.created_at.label("created_at"))
        if source == "import": query = query.join(MaterialImportRow, MaterialImportRow.batch_id == model.id).where(MaterialImportRow.material_id == material_id)
        else: query = query.where(model.material_id == material_id)
        if source == "record": query = query.where(model.kind == "MATERIAL")
        if source == "workflow":
            # Detailed immutable records above carry these successful changes.
            query = query.where(model.event_type.not_in(("CONTENT_SAVED", "AI_DRAFT_ADOPTED", "IDENTITY_COMPLETED")),
                or_(model.event_type != "SOURCE_METADATA_COMPLETED", model.result["audit"]["metadata_snapshot_id"].as_string().is_(None)))
        branches.append(query)
    history = union_all(*branches).subquery()
    query = select(history)
    if after is not None:
        try:
            source, raw_id = after.split(":", 1); identifier = UUID(raw_id)
            if source not in SOURCES: raise ValueError()
        except (ValueError, AttributeError): raise HTTPException(409, {"code": "HISTORY_CURSOR_INVALID"}) from None
        anchor = select(history.c.created_at).where(history.c.source == source, history.c.id == identifier)
        if session.scalar(anchor) is None: raise HTTPException(409, {"code": "HISTORY_CURSOR_INVALID"})
        # Use the stored scalar, not a rebound timestamp: SQLite's historical
        # CURRENT_TIMESTAMP values can lack fractions present in newer rows.
        created = anchor.scalar_subquery()
        query = query.where(or_(history.c.created_at < created,
            and_(history.c.created_at == created, or_(history.c.source < source,
                and_(history.c.source == source, history.c.id < identifier)))))
    heads = list(session.execute(query.order_by(history.c.created_at.desc(), history.c.source.desc(), history.c.id.desc()).limit(limit + 1)).mappings())
    items = [_event(session, row["source"], session.get(SOURCES[row["source"]], row["id"]), material_id) for row in heads[:limit]]
    return {"material_id": str(material_id), "items": items,
        "next_cursor": items[-1]["id"] if len(heads) > limit else None}
