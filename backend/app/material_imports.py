"""Historical import planning against current identities and permanent reservations."""
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select, tuple_
from sqlalchemy.exc import IntegrityError

from app.db.models import Company, InternalUser, MaterialFileOperation, MaterialNumberReservation, PBRMaterial, Project, PublishedBrand
from app.db.models import MaterialImportBatch, MaterialImportRow, PBRMaterialMetadata
from app.material_identity import ACTIVE_STATUSES, lock_folder_catalog, require_folder_idle
from app.material_review import canonical_hash


def _value(value):
    return value.isoformat() if isinstance(value, datetime) else str(value) if isinstance(value, UUID) else value


def _snapshots(records, fields):
    return [{key: _value(getattr(item, key)) for key in ("id", *fields, "updated_at")}
            for item in sorted(records.values(), key=lambda item: str(item.id))]


def _load(session, model, identifiers):
    return {item.id: item for item in session.scalars(select(model).where(model.id.in_(identifiers)))}


def source_context(payload, table):
    return {"source_sha256": table.source_sha256, "source": payload.source.options(),
            "columns": payload.columns.model_dump(exclude_none=True), "links": payload.links.model_dump(mode="json")}


def build_preview(session, payload, table, rows, findings):
    """Caller holds the exclusive application access gate for a consistent plan.

    Reference and identity queries use bounded input IDs/pairs. Optional folder
    links are checked against catalog paths and active source owners, without
    accessing the filesystem.
    """
    findings = list(findings)
    existing_paths = list(session.scalars(select(PBRMaterial.folder_path).where(PBRMaterial.folder_path.is_not(None)))) if any(row.folder_path for row in rows) else []
    batch_paths = [row.folder_path for row in rows if row.folder_path]
    projects = _load(session, Project, set(payload.links.projects.values()))
    brands = _load(session, PublishedBrand, set(payload.links.brands.values()))
    processors = _load(session, InternalUser, set(payload.links.processors.values()))
    company_ids = {item.company_id for item in (*projects.values(), *brands.values())}
    companies = _load(session, Company, company_ids)
    pairs = [(item.brand_id, item.sequence_number) for item in rows]
    existing_numbers, existing_identities, reserved_numbers = set(), set(), set()
    if rows:
        existing = session.execute(select(PBRMaterial.published_brand_id, PBRMaterial.sequence_number,
            PBRMaterial.technical_identity).where(or_(
                tuple_(PBRMaterial.published_brand_id, PBRMaterial.sequence_number).in_(pairs),
                PBRMaterial.technical_identity.in_([item.technical_identity for item in rows]))))
        for brand_id, number, identity in existing:
            existing_numbers.add((brand_id, number))
            existing_identities.add(identity)
        reserved_numbers = set(session.execute(select(MaterialNumberReservation.brand_id,
            MaterialNumberReservation.sequence_number).where(tuple_(MaterialNumberReservation.brand_id,
                MaterialNumberReservation.sequence_number).in_(pairs))).all())
    active_brands = set()
    for source, target in session.execute(select(MaterialFileOperation.source_brand_id,
            MaterialFileOperation.target_brand_id).where(MaterialFileOperation.status.in_(ACTIVE_STATUSES),
                or_(MaterialFileOperation.source_brand_id.in_(brands), MaterialFileOperation.target_brand_id.in_(brands)))):
        active_brands.update((source, target))
    for item in rows:
        def issue(field, code):
            findings.append({"row": item.source_row, "field": field, "code": code})
        project, brand, processor = projects.get(item.project_id), brands.get(item.brand_id), processors.get(item.processor_id)
        if item.project_id is not None and project is None: issue("project", "IMPORT_PROJECT_MISSING")
        if brand is None: issue("brand", "IMPORT_BRAND_MISSING")
        elif not brand.is_active: issue("brand", "IMPORT_BRAND_INACTIVE")
        if brand is not None and brand.folder_prefix != item.prefix:
            issue("identity", "IMPORT_BRAND_PREFIX_MISMATCH")
        if item.brand_id in active_brands: issue("brand", "IMPORT_BRAND_OPERATION_ACTIVE")
        if processor is None or not processor.is_active or processor.role != "PROCESSOR":
            issue("processor", "IMPORT_PROCESSOR_UNAVAILABLE")
        for field, record in (("project", project), ("brand", brand)):
            if record is not None and (record.company_id not in companies or not companies[record.company_id].is_active):
                issue(field, "IMPORT_COMPANY_UNAVAILABLE")
        if (item.brand_id, item.sequence_number) in existing_numbers or item.technical_identity in existing_identities:
            issue("identity", "IMPORT_MATERIAL_EXISTS")
        if (item.brand_id, item.sequence_number) in reserved_numbers:
            issue("identity", "IMPORT_NUMBER_RESERVED")
        if item.folder_path:
            path = item.folder_path.casefold()
            if any(path == other.casefold() or path.startswith(other.casefold() + "/") or other.casefold().startswith(path + "/")
                   for other in [*existing_paths, *[value for value in batch_paths if value != item.folder_path]]):
                issue("identity", "IMPORT_FOLDER_REFERENCE_CONFLICT")
            try:
                require_folder_idle(session, item.folder_path)
            except HTTPException:
                issue("identity", "IMPORT_FOLDER_REFERENCE_BUSY")
    references = {
        "projects": _snapshots(projects, ("name", "project_number", "company_id", "status")),
        "brands": _snapshots(brands, ("name", "folder_prefix", "brand_identifier", "company_id", "is_active", "next_sequence_number")),
        "processors": _snapshots(processors, ("display_name", "role", "is_active")),
        "companies": _snapshots(companies, ("name", "is_active")),
    }
    snapshot = {"schema_version": 1, **source_context(payload, table), "references": references,
                "rows": [item.public_values() for item in rows],
                "initial_state": {"workflow_status": "IN_PROGRESS", "validation_status": "NOT_CHECKED",
                                  "publication_status": "NOT_PUBLISHED", "is_published": False, "folder_path": None},
                "ignored_columns": [header for header in table.headers if header not in payload.columns.model_dump().values()]}
    return {"can_confirm": not findings and len(rows) == len(table.rows),
            "preview_hash": canonical_hash(snapshot) if not findings and len(rows) == len(table.rows) else None,
            "row_count": len(table.rows), "findings": findings, "snapshot": snapshot,
            "warnings": ["IMPORT_REQUIRES_NORMAL_REVIEW", *(["IMPORT_FOLDER_REFERENCES_UNVERIFIED"] if batch_paths else [])]}


def batch_summary(batch):
    return {"id": str(batch.id), "actor_id": str(batch.actor_id), "idempotency_key": str(batch.request_key),
            "source_sha256": batch.source_sha256, "preview_hash": batch.preview_hash,
            "source_format": batch.source_format, "row_count": batch.row_count,
            "reason": batch.reason, "created_at": batch.created_at}


def batch_result(session, batch):
    return {**batch_summary(batch), "snapshot": batch.snapshot,
            "rows": [{"material_id": str(row.material_id), **row.snapshot} for row in session.scalars(
                select(MaterialImportRow).where(MaterialImportRow.batch_id == batch.id).order_by(MaterialImportRow.source_row))]}


def confirm_import(session, actor_id, payload, table, rows, findings):
    """One DB transaction; caller holds the exclusive application access gate.

    Idempotent replay precedes conflict checks because its own original records
    now legitimately occupy their numbers. Never rebuild replay from mutable rows.
    """
    request_hash = canonical_hash({**source_context(payload, table), "reason": payload.reason,
        "expected_preview_hash": payload.expected_preview_hash, "acknowledge_unverified": payload.acknowledge_unverified})
    existing = session.scalar(select(MaterialImportBatch).where(MaterialImportBatch.actor_id == actor_id,
        MaterialImportBatch.request_key == payload.idempotency_key))
    if existing is not None:
        if existing.request_hash != request_hash:
            raise HTTPException(409, {"code": "IMPORT_IDEMPOTENCY_CONFLICT"})
        return batch_result(session, existing)
    if any(row.folder_path for row in rows):
        lock_folder_catalog(session)
    brands = {brand.id: brand for brand in session.scalars(select(PublishedBrand).where(
        PublishedBrand.id.in_({row.brand_id for row in rows})).order_by(PublishedBrand.id).with_for_update())}
    preview = build_preview(session, payload, table, rows, findings)
    if not preview["can_confirm"]:
        raise HTTPException(409, {"code": "IMPORT_BLOCKED", "findings": preview["findings"]})
    if preview["preview_hash"] != payload.expected_preview_hash:
        raise HTTPException(409, {"code": "IMPORT_PREVIEW_CHANGED"})
    snapshot = preview["snapshot"]
    batch = MaterialImportBatch(actor_id=actor_id, request_key=payload.idempotency_key, request_hash=request_hash,
        source_sha256=table.source_sha256, preview_hash=payload.expected_preview_hash, source_format=payload.source.format,
        row_count=len(rows), snapshot={key: value for key, value in snapshot.items() if key != "rows"}, reason=payload.reason)
    try:
        session.add(batch)
        session.flush()
        materials = []
        for row in rows:
            material = PBRMaterial(project_id=row.project_id, published_brand_id=row.brand_id,
                assigned_processor_id=row.processor_id, technical_identity=row.technical_identity,
                material_name=row.material_name, sequence_number=row.sequence_number, main_category_code=row.main_category_code,
                **{**snapshot["initial_state"], "folder_path": row.folder_path})
            material.metadata_state = PBRMaterialMetadata()
            session.add(material)
            materials.append((row, material))
            brands[row.brand_id].next_sequence_number = max(brands[row.brand_id].next_sequence_number, row.sequence_number + 1)
        session.flush()
        for row, material in materials:
            session.add(MaterialNumberReservation(brand_id=row.brand_id, sequence_number=row.sequence_number,
                material_id=material.id, actor_id=actor_id))
            session.add(MaterialImportRow(batch_id=batch.id, source_row=row.source_row, material_id=material.id,
                snapshot={**snapshot["initial_state"], **row.public_values()}))
        session.flush()
        result = batch_result(session, batch)
        session.commit()
        return result
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, {"code": "IMPORT_DATABASE_CONFLICT"}) from None
