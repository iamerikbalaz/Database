"""Offline CSV + ZIP publication, explicitly installed on the local desktop."""
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError
from sqlalchemy import select

from app.api.material_review import _material, _state
from app.auth.access import AccessDependency, CATALOG_MANAGERS
from app.auth.service import _aware
from app.db.models import MaterialAuditEvent, MaterialPackagingPolicy, PBRMaterialMetadata
from app.local_filesystem import LocalFilesError
from app.material_identity import require_material_idle
from app.material_review import canonical_hash, material_context
from app.packaging_settings import current_settings, lock_settings
from app.publication_content import content_review
from app.publication_csv import Digest, PublicationCsvRow, render_publication_csv
from app.publication_preflight import PublicationSelection
from app.resource_history import append_resource_change, resource_snapshot
from app.schemas import ApiSchema


class ExportCreate(PublicationSelection):
    expected_preview_hash: Digest
    destination_token: Annotated[str, Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
    idempotency_key: UUID


class ExportPublished(ApiSchema):
    idempotency_key: UUID


def prepare_local_publication(session, selection, access, settings, library, original_master_observations=None):
    actor = access.check(session, CATALOG_MANAGERS)
    lock_settings(session)
    config = current_settings(session, settings)
    items = []; snapshots = []; rows = []; materials = []; versions = []
    for identifier in selection.material_ids:
        material = _material(session, identifier, access, lock=True)
        require_material_idle(session, identifier)
        review = content_review(session, material)
        errors = list(review["errors"]); warnings = list(review["warnings"]); warning_fields = {}
        if material.workflow_status != "DONE": errors.append("MATERIAL_DONE_REQUIRED")
        if not material.folder_path: errors.append("FOLDER_REQUIRED")
        metadata = session.get(PBRMaterialMetadata, identifier)
        source = None
        if material.folder_path:
            access.require_folder(material.folder_path, material.technical_identity)
            try: source = library.metadata.inspect(material.folder_path)
            except Exception: errors.append("SOURCE_METADATA_UNAVAILABLE")
        if source is None or source.status not in {"VALID", "WARNING"} or source.sha256 is None:
            errors.append("METADATA_EXPORT_INVALID")
        if source and metadata and any(getattr(source, field) != (str(getattr(metadata, field)) if field in {"width_cm", "height_cm"} and getattr(metadata, field) is not None else getattr(metadata, field))
                for field in ("hex_color", "width_cm", "height_cm")):
            # Decimal spellings can differ (10.0 / 10.0000) while values agree.
            from decimal import Decimal
            def equal(field):
                left, right = getattr(source, field), getattr(metadata, field)
                if left is None or right is None: return left == right
                return Decimal(left) == Decimal(right) if field != "hex_color" else left == right
            if not all(equal(field) for field in ("hex_color", "width_cm", "height_cm")):
                errors.append("METADATA_SOURCE_DATABASE_MISMATCH")
        draft = review["snapshot"]["content"]
        row = None
        try:
            if source is not None:
                row = PublicationCsvRow(material_id=identifier, revision_hash=canonical_hash(material_context(material)),
                    content_context_hash=review["context_hash"], identity_name=material.technical_identity,
                    name=material.material_name, description=draft["description"], credits=draft["credits"],
                    width_cm=source.width_cm, height_cm=source.height_cm, color=source.hex_color,
                    brand_identifier=review["snapshot"]["brand"]["brand_identifier"],
                    categories=tuple(item["value"] for item in draft["categories"]), tags=tuple(draft["tags"]))
        except ValidationError as error:
            fields = {str(item["loc"][0]) for item in error.errors(include_input=False, include_context=False)}
            errors.extend("EXPORT_" + field.upper() + "_INVALID" for field in sorted(fields))
        if row:
            rows.append(row)
            csv_warnings = render_publication_csv([row]).warnings
            warnings = sorted(set(warnings + [item.code for item in csv_warnings]))
            warning_fields = {item.code: list(item.fields) for item in csv_warnings}
        original = session.scalar(select(MaterialPackagingPolicy).where(MaterialPackagingPolicy.material_id == identifier,
            MaterialPackagingPolicy.revision == 1))
        original_modified = original.evidence.get("master_last_modified_at") if original else None
        if original_modified is None:
            original_modified = (original_master_observations or {}).get(str(identifier))
        snapshot = {"material": resource_snapshot(material), "updated_at": _aware(material.updated_at).isoformat(),
            "content": review["snapshot"], "metadata_sha256": source.sha256 if source else None,
            "original_master_modified_at": original_modified}
        snapshots.append(snapshot)
        items.append({"material_id": str(identifier), "name": material.material_name, "identity_name": material.technical_identity,
            "row": row.model_dump(mode="json") if row else None, "errors": sorted(set(errors)),
            "warnings": [{"code": code, "fields": warning_fields.get(code, [])} for code in warnings]})
        versions.append({"id": str(identifier), "updated_at": _aware(material.updated_at).isoformat()})
        materials.append({"material_id": str(identifier), "identity_name": material.technical_identity,
            "folder_path": material.folder_path, "metadata_sha256": source.sha256 if source else None,
            "original_master_modified_at": snapshot["original_master_modified_at"]})
    identities = [item["identity_name"].casefold() for item in items]
    for item in items:
        if identities.count(item["identity_name"].casefold()) != 1: item["errors"].append("PUBLICATION_IDENTITY_COLLISION")
    digest = canonical_hash({"snapshots": snapshots, "settings": config})
    preview = {"items": items, "can_prepare": all(not item["errors"] for item in items), "preview_hash": digest}
    request = {"schema_version": 1, "materials": materials, "material_versions": versions,
        "preview_hash": digest, "settings": config,
        "csv_hex": render_publication_csv(rows).data.hex() if preview["can_prepare"] else None}
    return actor, preview, request


def build_local_publication_router(database, settings, adapter=None):
    router = APIRouter(prefix="/api/local-publication", tags=["offline publication"])

    def prepare(session, selection, access):
        return prepare_local_publication(session, selection, access, settings, adapter.library,
            getattr(adapter, "original_master_observations", None))

    def local(request):
        if adapter is None: raise HTTPException(503, {"code": "LOCAL_PUBLICATION_UNAVAILABLE"})
        if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(403, {"code": "LOCAL_DESKTOP_ONLY"})

    def call(action):
        try: return action()
        except LocalFilesError as error:
            raise HTTPException(404 if error.code == "LOCAL_EXPORT_NOT_FOUND" else 409, {"code": error.code}) from None
        except (OSError, ValueError): raise HTTPException(409, {"code": "LOCAL_EXPORT_UNAVAILABLE"}) from None

    def marked(session, identifier):
        return session.scalar(select(MaterialAuditEvent).where(MaterialAuditEvent.event_type == "LOCAL_PUBLICATION_MARKED_PUBLISHED",
            MaterialAuditEvent.request_hash == canonical_hash({"operation": "LOCAL_PUBLICATION_MARKED_PUBLISHED", "export_id": str(identifier)})))

    def view(session, value):
        return {"id": value["id"], "status": value["status"], "material_ids": value["material_ids"],
            "output_path": value["output_path"], "csv_name": value["csv_name"],
            "archives": [{"name": item["name"], "sha256": item["sha256"], "size_bytes": item["size"]} for item in value["archives"]],
            "error_code": value["error_code"], "published": marked(session, value["id"]) is not None,
            "issues": value.get("issues", [])}

    @router.post("/preview")
    def preview(payload: PublicationSelection, access: AccessDependency, request: Request):
        local(request)
        with database.session() as session:
            return prepare(session, payload, access)[1]

    @router.post("/destination")
    def destination(access: AccessDependency, request: Request):
        local(request)
        with database.session() as session: actor = access.check(session, CATALOG_MANAGERS)
        result = call(lambda: adapter.select_destination(actor.id))
        with database.session() as session: access.check(session, CATALOG_MANAGERS)
        return result

    @router.post("/exports")
    def create(payload: ExportCreate, access: AccessDependency, request: Request):
        local(request)
        # Replays use the original frozen request even if the material changed
        # afterwards; they never grant a new file operation or Published write.
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS)
            try: prior = adapter.status(payload.idempotency_key, actor.id)
            except LocalFilesError as error:
                if error.code != "LOCAL_EXPORT_NOT_FOUND": raise HTTPException(409, {"code": error.code}) from None
                prior = None
            if prior:
                for identifier in payload.material_ids: _material(session, identifier, access)
                frozen = adapter.frozen(payload.idempotency_key, actor.id)
                if frozen["preview_hash"] != payload.expected_preview_hash or sorted(prior["material_ids"]) != sorted(map(str, payload.material_ids)):
                    raise HTTPException(409, {"code": "LOCAL_EXPORT_REQUEST_CONFLICT"})
                return view(session, call(lambda: adapter.start(actor.id, payload.idempotency_key, payload.destination_token, frozen, lambda: None)))
            actor, prepared, frozen = prepare(session, payload, access)
            if prepared["preview_hash"] != payload.expected_preview_hash: raise HTTPException(409, {"code": "PUBLICATION_PREVIEW_CHANGED"})
            if not prepared["can_prepare"]: raise HTTPException(409, {"code": "PUBLICATION_INPUTS_BLOCKED", "preview": prepared})

        @contextmanager
        def verify():
            with database.session() as session:
                _, current, _ = prepare(session, payload, access)
                if current["preview_hash"] != payload.expected_preview_hash or not current["can_prepare"]:
                    raise LocalFilesError("LOCAL_EXPORT_MATERIAL_CHANGED")
                yield

        value = call(lambda: adapter.start(actor.id, payload.idempotency_key, payload.destination_token, frozen, verify))
        with database.session() as session:
            access.check(session, CATALOG_MANAGERS)
            return JSONResponse(status_code=202, content=view(session, value))

    @router.get("/exports/{identifier}")
    def status(identifier: UUID, access: AccessDependency, request: Request):
        local(request)
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS)
            value = call(lambda: adapter.status(identifier, actor.id))
            for material_id in value["material_ids"]: _material(session, UUID(material_id), access)
            return view(session, value)

    @router.post("/exports/{identifier}/published")
    def published(identifier: UUID, payload: ExportPublished, access: AccessDependency, request: Request):
        local(request)
        digest = canonical_hash({"operation": "LOCAL_PUBLICATION_MARKED_PUBLISHED", "export_id": str(identifier)})
        # Authorize before source IO, then repeat authorization under the final
        # transaction. No database locks remain held while hashing large maps.
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS)
            value = call(lambda: adapter.status(identifier, actor.id))
            if value["status"] != "COMPLETED": raise HTTPException(409, {"code": "LOCAL_EXPORT_NOT_COMPLETED"})
            for material_id in value["material_ids"]: _material(session, UUID(material_id), access)
            prior = session.scalar(select(MaterialAuditEvent).where(MaterialAuditEvent.actor_id == actor.id,
                MaterialAuditEvent.request_key == payload.idempotency_key))
            if prior and (prior.event_type != "LOCAL_PUBLICATION_MARKED_PUBLISHED" or prior.request_hash != digest):
                raise HTTPException(409, {"code": "LOCAL_EXPORT_REQUEST_CONFLICT"})
            if prior or marked(session, identifier): return {"published": True, "material_ids": value["material_ids"]}
        try:
            with adapter.unchanged_sources(identifier, actor.id), database.session() as session:
                actor = access.check(session, CATALOG_MANAGERS)
                lock_settings(session)
                materials = [_material(session, UUID(material_id), access, lock=True) for material_id in sorted(value["material_ids"])]
                prior = session.scalar(select(MaterialAuditEvent).where(MaterialAuditEvent.actor_id == actor.id,
                    MaterialAuditEvent.request_key == payload.idempotency_key))
                if prior and (prior.event_type != "LOCAL_PUBLICATION_MARKED_PUBLISHED" or prior.request_hash != digest):
                    raise HTTPException(409, {"code": "LOCAL_EXPORT_REQUEST_CONFLICT"})
                if prior or marked(session, identifier): return {"published": True, "material_ids": value["material_ids"]}
                selection = PublicationSelection(material_ids=[material.id for material in materials])
                _, current, _ = prepare(session, selection, access)
                if current["preview_hash"] != value["preview_hash"] or not current["can_prepare"]:
                    raise HTTPException(409, {"code": "LOCAL_EXPORT_MATERIAL_CHANGED"})
                for material in materials:
                    require_material_idle(session, material.id)
                    before = resource_snapshot(material)
                    if not material.is_published:
                        material.is_published = True; material.updated_at = datetime.now(timezone.utc)
                        append_resource_change(session, material, actor.id, before)
                body = {"published": True, "material_ids": value["material_ids"]}
                state = _state(session, materials[0].id)
                session.add(MaterialAuditEvent(material_id=materials[0].id, actor_id=actor.id,
                    event_type="LOCAL_PUBLICATION_MARKED_PUBLISHED", request_key=payload.idempotency_key, request_hash=digest,
                    generation=state.generation if state else 0, revision_hash=state.revision_hash if state else None,
                    result={"body": body, "audit": {"export_id": str(identifier), "material_ids": value["material_ids"]}}))
                session.commit()
                return body
        except LocalFilesError as error: raise HTTPException(409, {"code": error.code}) from None

    return router
