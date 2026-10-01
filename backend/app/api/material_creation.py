"""Create an explicit list with fixed numbers and recoverable local folders."""
from datetime import UTC, datetime
import hashlib
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, StringConstraints, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.access import AccessDependency, CATALOG_MANAGERS
from app.api.resources import _require_active_internal_user, _technical_identity
from app.catalog import ContentUpdate
from app.content_saves import apply_material_content
from app.db.material_creation_models import MaterialCreationBatch
from app.db.models import PBRMaterial, PBRMaterialMetadata, PublishedBrand, MaterialNumberReservation
from app.local_filesystem import LocalFilesError
from app.local_material_creation import LocalMaterialCreator
from app.material_assignment import require_order_customer
from app.main_category import require_current_category_code
from app.material_identity import require_material_idle, require_customer_rename_idle, lock_folder_catalog, require_folder_idle
from app.material_naming import name_component, material_name_from_identity
from app.material_review import canonical_hash
from app.path_settings import current_paths, list_sbs_templates, lock_paths, PathSettingsError
from app.resource_history import append_resource_change, resource_snapshot
from app.schemas import ApiSchema, CategoryCode


class MaterialBatchCreate(ApiSchema):
    idempotency_key: UUID
    expected_paths_version: Annotated[int, Field(strict=True, ge=0)]
    project_id: UUID | None = None
    published_brand_id: UUID
    assigned_processor_id: UUID
    main_category_code: CategoryCode
    names: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]] = Field(min_length=1, max_length=100)
    category_ids: list[UUID] = Field(default_factory=list, max_length=100)
    collection_ids: list[UUID] = Field(default_factory=list, max_length=100)
    resolution: Annotated[int, Field(strict=True, ge=1, le=32)]
    template_name: Annotated[str, StringConstraints(min_length=1, max_length=255)]

    @field_validator("names")
    @classmethod
    def names_are_one_column(cls, values):
        if any(any(char in value for char in "\t\r\n") for value in values):
            raise ValueError("Paste one column of material names, one name per line.")
        normalized = [name_component(value) for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("Material names in this batch must be distinct after normalization.")
        return values

    @model_validator(mode="after")
    def nonzero_key(self):
        if self.idempotency_key.int == 0:
            raise ValueError("Use a nonzero idempotency key.")
        return self


def batch_view(batch):
    return {"id": str(batch.id), "status": batch.status, "items": batch.items,
        "completed_count": sum(item["status"] == "COMPLETED" for item in batch.items), "total_count": len(batch.items)}


def build_material_creation_router(database, runtime, library=None):
    router = APIRouter(prefix="/api", tags=["material creation"])
    creator = LocalMaterialCreator(library) if library is not None else None

    def local(request):
        if library is None:
            raise HTTPException(503, {"code": "LOCAL_DESKTOP_UNAVAILABLE"})
        if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(403, {"code": "LOCAL_DESKTOP_ONLY"})

    def paths_for_creation(session):
        lock_paths(session)
        paths = current_paths(session, runtime)
        if not Path(paths["materials_root"]).is_relative_to(library.fs.root):
            raise HTTPException(409, {"code": "LOCAL_MATERIALS_ROOT_CHANGED",
                "message": "New material folders must stay inside the active desktop library."})
        return paths

    @router.get("/material-create-options")
    def options(access: AccessDependency, request: Request):
        with database.session() as session:
            access.check(session, CATALOG_MANAGERS)
            local(request)
            paths = paths_for_creation(session)
            try:
                templates = list_sbs_templates(paths["sbs_templates_root"])
            except (PathSettingsError, OSError):
                raise HTTPException(503, {"code": "SBS_TEMPLATES_UNAVAILABLE"}) from None
            return {"paths_version": paths["version"], "templates": templates}

    @router.get("/material-create-batches/{batch_id}")
    def get_batch(batch_id: UUID, access: AccessDependency):
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS)
            batch = session.get(MaterialCreationBatch, batch_id)
            if batch is None or batch.actor_id != actor.id:
                raise HTTPException(404, "Material creation batch not found.")
            return batch_view(batch)

    @router.get("/material-create-requests/{request_key}")
    def get_request(request_key: UUID, access: AccessDependency):
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS)
            batch = session.scalar(select(MaterialCreationBatch).where(MaterialCreationBatch.actor_id == actor.id,
                MaterialCreationBatch.request_key == request_key))
            if batch is None:
                raise HTTPException(404, "Material creation request not found.")
            return batch_view(batch)

    @router.post("/material-create-batches")
    def create(payload: MaterialBatchCreate, access: AccessDependency, request: Request):
        digest = canonical_hash(payload.model_dump(mode="json"))
        with database.session() as session:
            actor = access.check(session, CATALOG_MANAGERS)
            local(request)
            batch = session.scalar(select(MaterialCreationBatch).where(MaterialCreationBatch.actor_id == actor.id,
                MaterialCreationBatch.request_key == payload.idempotency_key).with_for_update())
            if batch:
                if batch.request_hash != digest:
                    raise HTTPException(409, {"code": "MATERIAL_CREATION_KEY_REUSED"})
                if batch.status == "COMPLETED":
                    return batch_view(batch)
                batch_id = batch.id
            else:
                require_current_category_code(session, payload.main_category_code)
                paths = paths_for_creation(session)
                if paths["version"] != payload.expected_paths_version:
                    raise HTTPException(409, {"code": "PATHS_VERSION_CHANGED"})
                require_order_customer(session, payload.project_id, payload.published_brand_id)
                _require_active_internal_user(session, payload.assigned_processor_id)
                brand = session.scalar(select(PublishedBrand).where(PublishedBrand.id == payload.published_brand_id).with_for_update())
                if brand is None or not brand.is_active or not brand.is_customer:
                    raise HTTPException(409, {"code": "MATERIAL_CUSTOMER_INACTIVE"})
                require_customer_rename_idle(session, brand.id)
                if brand.next_sequence_number + len(payload.names) - 1 > 9999:
                    raise HTTPException(409, {"code": "MATERIAL_SEQUENCE_EXHAUSTED"})
                try:
                    raw = creator.read_template(paths["sbs_templates_root"], payload.template_name)
                except (PathSettingsError, LocalFilesError, OSError):
                    raise HTTPException(409, {"code": "SBS_TEMPLATE_UNAVAILABLE"}) from None
                context = {"materials_root": paths["materials_root"], "paths_version": paths["version"],
                    "template_name": payload.template_name, "template_sha256": hashlib.sha256(raw).hexdigest(),
                    "resolution": payload.resolution, "customer_folder": brand.folder_prefix}
                prefix = Path(paths["materials_root"]).relative_to(library.fs.root)
                batch_id = uuid4()
                items = []
                for offset, name in enumerate(payload.names):
                    number = brand.next_sequence_number + offset
                    identity = _technical_identity(brand, number, payload.main_category_code, name)
                    material = PBRMaterial(project_id=payload.project_id, published_brand_id=brand.id,
                        assigned_processor_id=payload.assigned_processor_id, material_name=material_name_from_identity(identity),
                        main_category_code=payload.main_category_code, sequence_number=number,
                        source_brand_name=brand.name, technical_identity=identity)
                    material.metadata_state = PBRMaterialMetadata()
                    session.add(material); session.flush()
                    session.add(MaterialNumberReservation(brand_id=brand.id, sequence_number=number, material_id=material.id, actor_id=actor.id))
                    apply_material_content(session, material, ContentUpdate(idempotency_key=material.id, expected_revision=0,
                        category_ids=payload.category_ids, collection_ids=payload.collection_ids), actor.id)
                    append_resource_change(session, material, actor.id, {}, action="CREATED")
                    items.append({"material_id": str(material.id), "name": material.material_name,
                        "technical_identity": identity, "folder_path": (prefix / brand.folder_prefix / identity).as_posix(),
                        "status": "PENDING", "error_code": None})
                brand.next_sequence_number += len(items)
                batch = MaterialCreationBatch(id=batch_id, actor_id=actor.id, customer_id=brand.id,
                    request_key=payload.idempotency_key, request_hash=digest, request_payload=payload.model_dump(mode="json"),
                    source_context=context, items=items, status="PENDING")
                session.add(batch)
                try:
                    creator.capture_template(batch_id, context, raw)
                    session.commit()
                except (LocalFilesError, OSError):
                    session.rollback()
                    raise HTTPException(409, {"code": "MATERIAL_CREATION_STAGING_UNAVAILABLE"}) from None
                except IntegrityError:
                    session.rollback()
                    raise HTTPException(409, {"code": "MATERIAL_CREATION_CONFLICT"}) from None
        # Each created record has a fixed number before any public folder IO.
        # A failed item retains its record/number and is retried by the same key.
        for index in range(len(payload.names)):
            with database.session() as session:
                actor = access.check(session, CATALOG_MANAGERS)
                paths_for_creation(session)
                batch = session.scalar(select(MaterialCreationBatch).where(MaterialCreationBatch.id == batch_id).with_for_update())
                if batch.status == "COMPLETED":
                    return batch_view(batch)
                item = batch.items[index]
                if item["status"] == "COMPLETED":
                    continue
                material = session.scalar(select(PBRMaterial).where(PBRMaterial.id == UUID(item["material_id"])).with_for_update())
                access.require_material(material)
                require_material_idle(session, material.id)
                lock_folder_catalog(session)
                require_folder_idle(session, item["folder_path"])
                before = resource_snapshot(material)
                if material.technical_identity != item["technical_identity"] or material.published_brand_id != batch.customer_id or material.folder_path not in (None, item["folder_path"]):
                    failure = "MATERIAL_CREATION_RECORD_CHANGED"
                else:
                    try:
                        creator.create_folder(batch.id, item, batch.source_context)
                        material.folder_path = item["folder_path"]
                        material.updated_at = datetime.now(UTC)
                        append_resource_change(session, material, actor.id, before)
                        failure = None
                    except (LocalFilesError, OSError, ValueError) as error:
                        failure = error.code if isinstance(error, LocalFilesError) else "MATERIAL_FOLDER_CREATE_FAILED"
                items = [dict(value) for value in batch.items]
                items[index] = {**item, "status": "FAILED" if failure else "COMPLETED", "error_code": failure}
                batch.items = items
                batch.status = "COMPLETED" if all(value["status"] == "COMPLETED" for value in items) else "PARTIAL"
                session.commit()
        with database.session() as session:
            access.check(session, CATALOG_MANAGERS)
            return batch_view(session.get(MaterialCreationBatch, batch_id))

    return router
