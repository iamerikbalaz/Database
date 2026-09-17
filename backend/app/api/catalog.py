"""Audited catalog vocabulary and per-material publication drafts."""
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.material_review import _material
from app.auth.access import AccessDependency, CATALOG_MANAGERS
from app.catalog import CategoryCreate, CollectionCreate, CatalogActivityUpdate, ContentUpdate, value_key
from app.db.models import (OnlineCategory, BrandCollection, CatalogAuditEvent,
    MaterialOnlineCategory, MaterialCollection, MaterialContentRevision, PBRMaterial, PublishedBrand)
from app.material_identity import require_material_idle
from app.material_review import canonical_hash, invalidate_review
from app.publication_content import catalog_view, content_view
from app.content_saves import save_material_content


def _conflict(code):
    raise HTTPException(409, {"code": code})



def build_catalog_router(database):
    router = APIRouter(prefix="/api", tags=["catalog content"])

    @router.get("/online-categories")
    def categories(access: AccessDependency):
        with database.session() as session:
            access.check(session)
            return [catalog_view(item) for item in session.scalars(select(OnlineCategory).order_by(OnlineCategory.normalized_key, OnlineCategory.id))]

    @router.get("/collections")
    def collections(access: AccessDependency, brand_id: UUID | None = None):
        with database.session() as session:
            access.check(session)
            query = select(BrandCollection).order_by(BrandCollection.normalized_key, BrandCollection.id)
            if brand_id is not None:
                query = query.where(BrandCollection.brand_id == brand_id)
            return [catalog_view(item) for item in session.scalars(query)]

    def change_catalog(kind, payload, access, item_id=None):
        model = OnlineCategory if kind == "CATEGORY" else BrandCollection
        request_hash = canonical_hash({"kind": kind, "id": str(item_id) if item_id else None, "payload": payload.model_dump(mode="json")})
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
            if item_id is None:
                if kind == "COLLECTION":
                    brand = session.get(PublishedBrand, payload.brand_id)
                    if brand is None:
                        raise HTTPException(404, "Published brand not found.")
                    if not brand.is_active:
                        _conflict("CATALOG_BRAND_INACTIVE")
                item = model(value=payload.value, normalized_key=value_key(payload.value))
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
                if item.is_active != payload.is_active:
                    link = MaterialOnlineCategory if kind == "CATEGORY" else MaterialCollection
                    column = link.category_id if kind == "CATEGORY" else link.collection_id
                    for material in session.scalars(select(PBRMaterial).join(link).where(column == item.id)
                            .order_by(PBRMaterial.id).with_for_update(of=PBRMaterial)):
                        require_material_idle(session, material.id)
                        invalidate_review(session, material, actor.id, "CATALOG_ACTIVITY_CHANGED")
                    item.is_active = payload.is_active
                    item.version += 1
                audit = {"action": "ACTIVITY_CHANGED", "reason": payload.reason}
            try:
                session.flush()
                body = catalog_view(item); code = 201 if item_id is None else 200
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

    @router.get("/catalog-audit")
    def catalog_audit(access: AccessDependency):
        with database.session() as session:
            access.check(session, CATALOG_MANAGERS)
            return [{"id": item.id, "resource_id": item.resource_id, "resource_kind": item.resource_kind,
                "actor_id": item.actor_id, "created_at": item.created_at, "details": item.result["audit"]}
                for item in session.scalars(select(CatalogAuditEvent).order_by(CatalogAuditEvent.created_at.desc(), CatalogAuditEvent.id.desc()).limit(100))]

    @router.get("/materials/{material_id}/content")
    def get_content(material_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session)
            return content_view(session, _material(session, material_id, access, lock=True))

    @router.get("/materials/{material_id}/content-history")
    def content_history(material_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session); _material(session, material_id, access)
            return [{"id": item.id, "revision": item.revision, "actor_id": item.actor_id, "snapshot": item.snapshot,
                "snapshot_hash": item.snapshot_hash, "reason": item.reason, "created_at": item.created_at}
                for item in session.scalars(select(MaterialContentRevision).where(MaterialContentRevision.material_id == material_id)
                    .order_by(MaterialContentRevision.revision.desc()).limit(100))]

    @router.post("/materials/{material_id}/content")
    def save_content(material_id: UUID, payload: ContentUpdate, access: AccessDependency):
        return save_material_content(database, material_id, payload, access)

    return router
