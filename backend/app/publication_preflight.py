"""Freeze candidate publication facts under domain locks; never read source IO."""
from collections import Counter
import json
from dataclasses import asdict, dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import Field, ValidationError, field_validator
from sqlalchemy import select

from app.api.material_approvals import PUBLICATION_APPROVERS, _approvals
from app.api.material_review import _material, _state
from app.auth.access import AccessDependency
from app.catalog import value_key
from app.db.models import MaterialInventory, MaterialTechnicalCheck, PBRMaterialMetadata, PBRMaterialMetadataSnapshot
from app.material_identity import require_material_idle
from app.material_review import canonical_hash, material_context
from app.technical_client import TechnicalReport
from app.packaging_settings import current_settings, lock_settings
from app.publication_content import content_review
from app.publication_csv import MAX_BATCH_SIZE, PublicationCsvRow, render_publication_csv
from app.schemas import ApiSchema


class PublicationSelection(ApiSchema):
    material_ids: Annotated[list[UUID], Field(min_length=1, max_length=MAX_BATCH_SIZE)]

    @field_validator("material_ids")
    @classmethod
    def unique_ordered_selection(cls, values):
        if len(set(values)) != len(values): raise ValueError("Select each material once")
        return sorted(values)


@dataclass
class PreparedPublication:
    preview: dict
    snapshots: dict[UUID, dict]
    rows: tuple[PublicationCsvRow, ...]


def _candidate(session, material, *, packaging_execution_id=None, staging_job_id=None, packaging_settings=None):
    if material.is_archived: raise HTTPException(409, {"code": "MATERIAL_ARCHIVED"})
    errors = []
    try: require_material_idle(session, material.id, packaging_execution_id=packaging_execution_id, staging_job_id=staging_job_id)
    except HTTPException as error:
        if error.status_code != 409: raise
        errors.append("MATERIAL_OPERATION_ACTIVE")
    if material.workflow_status != "DONE": errors.append("PRODUCTION_DONE_REQUIRED")
    state = _state(session, material.id)
    inventory = session.get(MaterialInventory, state.inventory_id) if state and state.inventory_id else None
    check = session.get(MaterialTechnicalCheck, state.technical_check_id) if state and state.technical_check_id else None
    observed = bool(state and state.revision_hash and inventory and check
        and inventory.material_id == material.id and inventory.generation == state.generation
        and inventory.revision_hash == state.revision_hash and check.material_id == material.id
        and check.generation == state.generation and check.revision_hash == state.revision_hash
        and check.inventory_id == inventory.id and not state.failure_code
        and check.report.get("can_approve") is True and not check.report.get("errors"))
    if observed:
        try:
            verified = TechnicalReport.model_validate_json(json.dumps({**check.report, "inventory": inventory.source_inventory}))
            observed = (verified.can_approve and canonical_hash(check.report) == check.report_hash
                and inventory.material_context == material_context(material)
                and verified.inventory.folder_name == material.technical_identity
                and canonical_hash({"source_files_hash": verified.inventory.source_revision_hash,
                    "material_context": material_context(material)}) == state.revision_hash)
        except (ValueError, TypeError, KeyError): observed = False
    if not observed: errors.append("CURRENT_TECHNICAL_REVIEW_REQUIRED")
    approvals = {item.kind: item for item in _approvals(session, material.id, state)}
    technical = approvals.get("TECHNICAL")
    publication = approvals.get("PUBLICATION")
    content = content_review(session, material)
    errors.extend(content["errors"])
    current = session.get(PBRMaterialMetadata, material.id)
    metadata = session.get(PBRMaterialMetadataSnapshot, current.current_snapshot_id) if current and current.current_snapshot_id else None
    if metadata is None or metadata.material_id != material.id:
        errors.append("METADATA_SNAPSHOT_REQUIRED")
        metadata = None
    else:
        fields = ("status", "source_filename", "source_sha256", "hex_color", "width_cm", "height_cm", "master_resolution")
        if any(getattr(current, field) != getattr(metadata, field) for field in fields):
            errors.append("METADATA_CURRENT_SNAPSHOT_MISMATCH")
        if metadata.status not in {"VALID", "WARNING"}: errors.append("METADATA_EXPORT_INVALID")
        source_entries = inventory.source_inventory["entries"] if inventory else []
        source_filename = "metadata.json" if any(entry["path"] == "metadata.json" for entry in source_entries) else "metadata.txt"
        source = next((entry for entry in source_entries
            if entry["path"] == source_filename and entry["kind"] == "file"), None)
        if (metadata.source_filename != source_filename or not metadata.source_sha256
                or source is None or source["sha256"] != metadata.source_sha256):
            errors.append("METADATA_SOURCE_REVISION_MISMATCH")
        if inventory and metadata.master_resolution != inventory.source_inventory["master_resolution"]:
            errors.append("METADATA_MASTER_REVISION_MISMATCH")
    draft = content["snapshot"]["content"]
    row = None
    if metadata and state and state.revision_hash:
        try:
            row = PublicationCsvRow(material_id=material.id, revision_hash=state.revision_hash,
                content_context_hash=content["context_hash"], identity_name=material.technical_identity,
                name=material.material_name, description=draft["description"], credits=draft["credits"],
                width_cm=metadata.width_cm, height_cm=metadata.height_cm,
                brand_identifier=content["snapshot"]["brand"]["brand_identifier"],
                categories=tuple(item["value"] for item in draft["categories"]), color=metadata.hex_color,
                tags=tuple(draft["tags"]))
        except ValidationError as error:
            # Only schema-owned field names; never include values or parser messages.
            fields = {item["loc"][0] for item in error.errors(include_input=False, include_context=False)}
            errors.extend("EXPORT_" + field.upper() + "_INVALID" for field in sorted(fields))
    warnings = [{"material_id": str(material.id), "code": code, "fields": []} for code in content["warnings"]]
    if row:
        warnings = [{**asdict(item), "material_id": str(item.material_id), "fields": list(item.fields)}
            for item in render_publication_csv([row]).warnings]
    snapshot = {"schema_version": 1, "material": {**material_context(material), "workflow_status": material.workflow_status,
        "publication_status": material.publication_status, "is_published": material.is_published},
        "content_context_hash": content["context_hash"], "content_snapshot": content["snapshot"],
        "content_approval_id": content["approval"]["id"] if content["approval"] else None,
        "generation": state.generation if state else 0, "revision_hash": state.revision_hash if state else None,
        "inventory_id": str(inventory.id) if inventory else None,
        "source_revision_hash": inventory.source_inventory["source_revision_hash"] if inventory else None,
        "technical_check_id": str(check.id) if check else None, "technical_report_hash": check.report_hash if check else None,
        "technical_approval_id": str(technical.id) if technical else None,
        "publication_approval_id": str(publication.id) if publication else None,
        "metadata_snapshot_id": str(metadata.id) if metadata else None,
        "metadata_source_sha256": metadata.source_sha256 if metadata else None,
        "csv_row": row.model_dump(mode="json") if row else None,
        "errors": sorted(set(errors)), "warnings": warnings}
    if packaging_settings is not None:
        snapshot.update(schema_version=2, packaging_settings=packaging_settings)
    return snapshot, row


def prepare_publication(session, selection: PublicationSelection, access, *, packaging_settings=None) -> PreparedPublication:
    """Caller authenticates under the shared domain gate before acquiring rows."""
    materials = [_material(session, identifier, access, lock=True) for identifier in selection.material_ids]
    identities = Counter(value_key(material.technical_identity) for material in materials)
    snapshots = {}
    rows = []
    items = []
    for material in materials:
        snapshot, row = _candidate(session, material, packaging_settings=packaging_settings)
        if identities[value_key(material.technical_identity)] > 1:
            snapshot["errors"] = sorted([*snapshot["errors"], "PUBLICATION_IDENTITY_COLLISION"])
        snapshots[material.id] = snapshot
        if row: rows.append(row)
        items.append({"material_id": str(material.id), "identity_name": material.technical_identity,
            "name": material.material_name, "snapshot_hash": canonical_hash(snapshot),
            "revision_hash": snapshot["revision_hash"], "content_context_hash": snapshot["content_context_hash"],
            "row": snapshot["csv_row"], "errors": snapshot["errors"], "warnings": snapshot["warnings"]})
    preview = {"items": items, "can_prepare": all(not item["errors"] for item in items),
        "preview_hash": canonical_hash({"schema_version": 1, "snapshots": list(snapshots.values())})}
    return PreparedPublication(preview, snapshots, tuple(rows))


def build_publication_preview_router(database, settings):
    router = APIRouter(prefix="/api/publication-batches", tags=["publication preparation"])

    @router.post("/preview")
    def preview(payload: PublicationSelection, access: AccessDependency):
        with database.session() as session:
            access.check(session, PUBLICATION_APPROVERS)
            lock_settings(session)
            prepared = prepare_publication(session, payload, access, packaging_settings=current_settings(session, settings))
            access.check(session, PUBLICATION_APPROVERS)
            return prepared.preview

    return router
