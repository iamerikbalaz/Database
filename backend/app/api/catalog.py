"""Audited catalog vocabulary and per-material publication drafts."""
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.api.material_review import _material
from app.auth.access import AccessDependency, CATALOG_MANAGERS
from app.catalog import CategoryCreate, CollectionCreate, CatalogActivityUpdate, CatalogTableUpdate, CatalogIdentityUpdate, ContentUpdate, value_key
from app.db.models import (OnlineCategory, OnlineCategoryCode, BrandCollection, CatalogAuditEvent,
    MaterialOnlineCategory, MaterialCollection, MaterialContentRevision, MaterialMetadataOperation, PBRMaterial, PublishedBrand)
from app.material_identity import require_material_idle
from app.material_review import canonical_hash, invalidate_review
from app.publication_content import catalog_view, content_view
from app.content_saves import save_material_content
from app.history_pagination import HistoryLimit, history_window
from app.main_category import CATEGORY_PATHS, required_category, reserve_category_codes


def _conflict(code):
    raise HTTPException(409, {"code": code})



def build_catalog_router(database):
    router = APIRouter(prefix="/api", tags=["catalog content"])

    @router.get("/online-categories")
    def categories(access: AccessDependency):
        with database.session() as session:
            access.check(session)
            aliases = {}
            for alias in session.scalars(select(OnlineCategoryCode).order_by(OnlineCategoryCode.code)):
                aliases.setdefault(alias.category_id, []).append(alias.code)
            return [{**catalog_view(item, details=True), "aliases": aliases.get(item.id, [])} for item in session.scalars(select(OnlineCategory).order_by(OnlineCategory.normalized_key, OnlineCategory.id))]

    @router.get("/collections")
    def collections(access: AccessDependency, brand_id: UUID | None = None):
        with database.session() as session:
            access.check(session)
            query = select(BrandCollection).order_by(BrandCollection.normalized_key, BrandCollection.id)
            if brand_id is not None:
                query = query.where(BrandCollection.brand_id == brand_id)
            return [catalog_view(item, details=True) for item in session.scalars(query)]

    def change_catalog(kind, payload, access, item_id=None, *, details=False):
        model = OnlineCategory if kind == "CATEGORY" else BrandCollection
        # Omitted new fields preserve the hash of pre-upgrade create requests.
        request_data = {"kind": kind, "id": str(item_id) if item_id else None, "payload": payload.model_dump(mode="json", exclude_unset=True)}
        if details:
            request_data["details"] = True
        request_hash = canonical_hash(request_data)
        with database.session() as session:
            # Rare vocabulary changes serialize with domain writes. This lets a
            # deactivation invalidate every current user of that value atomically.
            actor = access.check(session, CATALOG_MANAGERS, exclusive=True)
            event = session.scalar(select(CatalogAuditEvent).where(CatalogAuditEvent.actor_id == actor.id,
                CatalogAuditEvent.request_key == payload.idempotency_key))
            if event:
                if event.request_hash != request_hash:
                    _conflict("CATALOG_REQUEST_KEY_REUSED")
                return JSONResponse(status_code=event.result["status_code"], content=event.result["body"])
            pending_required = []
            reserved_codes = set()
            if kind == "CATEGORY" and (item_id is None or "abbreviation" in payload.model_fields_set):
                # A new mapping can replace a computed required-category UUID.
                # Keep that UUID stable while a durable combined save owns its
                # immutable content request, including when no catalog row was
                # linked yet. The exclusive access gate covers both projections.
                for operation in session.scalars(select(MaterialMetadataOperation).where(MaterialMetadataOperation.status == "RUNNING")):
                    if operation.request_payload.get("content") is not None:
                        material = session.get(PBRMaterial, operation.material_id)
                        if material is not None:
                            pending_required.append((material, required_category(session, material)["id"]))
            if item_id is None:
                if kind == "COLLECTION":
                    brand = session.get(PublishedBrand, payload.brand_id)
                    if brand is None:
                        raise HTTPException(404, "Published brand not found.")
                    if not brand.is_active:
                        _conflict("CATALOG_BRAND_INACTIVE")
                item = model(value=payload.value, normalized_key=value_key(payload.value), abbreviation=payload.abbreviation)
                if kind == "COLLECTION":
                    item.brand_id = payload.brand_id
                session.add(item)
                audit = {"action": "CREATED"}
            else:
                item = session.get(model, item_id)
                if item is None:
                    raise HTTPException(404, "Catalog value not found.")
                if item.version != payload.expected_version:
                    _conflict("CATALOG_VERSION_CHANGED")
                identity_edit = isinstance(payload, CatalogIdentityUpdate)
                property_name = "identity" if identity_edit else "abbreviation" if "abbreviation" in payload.model_fields_set else "is_active"
                next_value = {"value": payload.value, "abbreviation": payload.abbreviation} if identity_edit else getattr(payload, property_name)
                previous_value = {"value": item.value, "abbreviation": item.abbreviation} if identity_edit else getattr(item, property_name)
                if previous_value != next_value:
                    pending_field = "category_ids" if kind == "CATEGORY" else "collection_ids"
                    for operation in session.scalars(select(MaterialMetadataOperation).where(MaterialMetadataOperation.status == "RUNNING")):
                        content = operation.request_payload.get("content") or {}
                        material = session.get(PBRMaterial, operation.material_id)
                        required_id = required_category(session, material)["catalog_id"] if kind == "CATEGORY" and material else None
                        if str(item.id) in content.get(pending_field, []) or str(item.id) == required_id:
                            _conflict("MATERIAL_OPERATION_ACTIVE")
                    link = MaterialOnlineCategory if kind == "CATEGORY" else MaterialCollection
                    column = link.category_id if kind == "CATEGORY" else link.collection_id
                    linked_ids = set(session.scalars(select(link.material_id).where(column == item.id)))
                    condition = PBRMaterial.id.in_(linked_ids)
                    if kind == "CATEGORY":
                        codes = {code for code, path in CATEGORY_PATHS.items() if value_key(path) == item.normalized_key}
                        if item.abbreviation:
                            codes.add(item.abbreviation)
                        codes.update(session.scalars(select(OnlineCategoryCode.code).where(OnlineCategoryCode.category_id == item.id)))
                        condition = or_(condition, PBRMaterial.main_category_code.in_(codes))
                        # Capture the original projection before either label or code changes.
                        for code in codes:
                            probe = type("CategoryProbe", (), {"main_category_code": code})()
                            if required_category(session, probe)["catalog_id"] == str(item.id):
                                reserved_codes.add(code)
                    for material in session.scalars(select(PBRMaterial).where(condition).order_by(PBRMaterial.id).with_for_update(of=PBRMaterial)):
                        if kind == "CATEGORY" and material.id not in linked_ids and required_category(session, material)["catalog_id"] != str(item.id):
                            continue
                        require_material_idle(session, material.id)
                        invalidate_review(session, material, actor.id, "CATALOG_ACTIVITY_CHANGED" if property_name == "is_active" else "CATALOG_IDENTITY_CHANGED" if identity_edit else "CATALOG_ABBREVIATION_CHANGED")
                    if identity_edit:
                        # Catalog labels/codes affect future material choices. Historical
                        # material identity and data folders are never renamed here.
                        item.value = payload.value
                        item.normalized_key = value_key(payload.value)
                        item.abbreviation = payload.abbreviation
                    else:
                        setattr(item, property_name, next_value)
                    item.version += 1
                audit = {"action": "ACTIVITY_CHANGED" if property_name == "is_active" else "IDENTITY_CHANGED" if identity_edit else "ABBREVIATION_CHANGED", "reason": payload.reason or "Catalog property updated",
                         "property": property_name, "before": previous_value, "after": next_value}
            try:
                session.flush()
                if kind == "CATEGORY":
                    reserved_codes.add(item.abbreviation)
                    # Newly created canonical names can acquire an existing synthetic code.
                    for code, path in CATEGORY_PATHS.items():
                        if value_key(path) == item.normalized_key:
                            probe = type("CategoryProbe", (), {"main_category_code": code})()
                            if required_category(session, probe)["catalog_id"] == str(item.id):
                                reserved_codes.add(code)
                    reserve_category_codes(session, item, reserved_codes)
                    session.flush()
                if any(required_category(session, material)["id"] != category_id
                        for material, category_id in pending_required):
                    _conflict("MATERIAL_OPERATION_ACTIVE")
                body = catalog_view(item, details=details); code = 201 if item_id is None else 200
                if kind == "CATEGORY" and details:
                    body["aliases"] = list(session.scalars(select(OnlineCategoryCode.code).where(OnlineCategoryCode.category_id == item.id).order_by(OnlineCategoryCode.code)))
                session.add(CatalogAuditEvent(resource_id=item.id, resource_kind=kind, actor_id=actor.id,
                    request_key=payload.idempotency_key, request_hash=request_hash,
                    result={"status_code": code, "body": body, "audit": audit}))
                session.commit()
            except IntegrityError:
                session.rollback()
                _conflict("CATALOG_VALUE_EXISTS_OR_CONFLICT")
            return JSONResponse(status_code=code, content=body)

    @router.post("/online-categories")
    def create_category(payload: CategoryCreate, access: AccessDependency):
        return change_catalog("CATEGORY", payload, access)

    @router.post("/collections")
    def create_collection(payload: CollectionCreate, access: AccessDependency):
        return change_catalog("COLLECTION", payload, access)

    @router.patch("/online-categories/{item_id}")
    def update_category(item_id: UUID, payload: CatalogActivityUpdate, access: AccessDependency):
        return change_catalog("CATEGORY", payload, access, item_id)

    @router.patch("/collections/{item_id}")
    def update_collection(item_id: UUID, payload: CatalogActivityUpdate, access: AccessDependency):
        return change_catalog("COLLECTION", payload, access, item_id)

    @router.patch("/online-categories/{item_id}/table")
    def update_category_table(item_id: UUID, payload: CatalogTableUpdate, access: AccessDependency):
        return change_catalog("CATEGORY", payload, access, item_id, details=True)

    @router.patch("/collections/{item_id}/table")
    def update_collection_table(item_id: UUID, payload: CatalogTableUpdate, access: AccessDependency):
        return change_catalog("COLLECTION", payload, access, item_id, details=True)

    @router.patch("/online-categories/{item_id}/identity")
    def update_category_identity(item_id: UUID, payload: CatalogIdentityUpdate, access: AccessDependency):
        return change_catalog("CATEGORY", payload, access, item_id, details=True)

    @router.patch("/collections/{item_id}/identity")
    def update_collection_identity(item_id: UUID, payload: CatalogIdentityUpdate, access: AccessDependency):
        return change_catalog("COLLECTION", payload, access, item_id, details=True)

    @router.get("/catalog-audit")
    def catalog_audit(access: AccessDependency, after: UUID | None = None, limit: HistoryLimit = 100):
        with database.session() as session:
            access.check(session, CATALOG_MANAGERS)
            return [{"id": item.id, "resource_id": item.resource_id, "resource_kind": item.resource_kind,
                "actor_id": item.actor_id, "created_at": item.created_at, "details": item.result["audit"]}
                for item in history_window(session, CatalogAuditEvent, after=after, limit=limit)]

    @router.get("/materials/{material_id}/content")
    def get_content(material_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session)
            return content_view(session, _material(session, material_id, access, lock=True))

    @router.get("/materials/{material_id}/content-history")
    def content_history(material_id: UUID, access: AccessDependency, after: UUID | None = None, limit: HistoryLimit = 100):
        with database.session() as session:
            access.check(session); _material(session, material_id, access)
            return [{"id": item.id, "revision": item.revision, "actor_id": item.actor_id, "snapshot": item.snapshot,
                "snapshot_hash": item.snapshot_hash, "reason": item.reason, "created_at": item.created_at}
                for item in history_window(session, MaterialContentRevision,
                    conditions=(MaterialContentRevision.material_id == material_id,), after=after, limit=limit, order="revision")]

    @router.post("/materials/{material_id}/content")
    def save_content(material_id: UUID, payload: ContentUpdate, access: AccessDependency):
        return save_material_content(database, material_id, payload, access)

    return router
