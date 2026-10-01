from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, HTTPException
from pydantic import Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.access import ADMIN, CATALOG_MANAGERS, AccessDependency
from app.db.path_settings_models import PathsSettingsRevision
from app.db.notion_sync_models import OrderFolderOperation
from app.material_review import canonical_hash
from app.path_settings import current_paths, list_sbs_templates, lock_paths, PathSettingsError, validate_paths
from app.schemas import ApiSchema

DirectoryPath = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]


class PathsSettingsUpdate(ApiSchema):
    idempotency_key: UUID
    expected_version: Annotated[int, Field(strict=True, ge=0)]
    sbs_templates_root: DirectoryPath
    orders_root: DirectoryPath
    materials_root: DirectoryPath


def build_path_settings_router(database, runtime):
    router = APIRouter(prefix="/api/settings/paths", tags=["settings"])

    @router.get("")
    def current(access: AccessDependency):
        with database.session() as session:
            access.check(session, ADMIN)
            return current_paths(session, runtime)

    @router.get("/sbs-templates")
    def templates(access: AccessDependency):
        with database.session() as session:
            access.check(session, CATALOG_MANAGERS)
            try:
                return {"items": list_sbs_templates(current_paths(session, runtime)["sbs_templates_root"])}
            except PathSettingsError as error:
                raise HTTPException(503, {"code": "SBS_TEMPLATES_UNAVAILABLE", "message": str(error)}) from None

    @router.post("")
    def update(payload: PathsSettingsUpdate, access: AccessDependency):
        digest = canonical_hash(payload.model_dump(mode="json"))
        with database.session() as session:
            actor = access.check(session, ADMIN)
            lock_paths(session, exclusive=True)
            actor = access.check(session, ADMIN)
            prior = session.scalar(select(PathsSettingsRevision).where(PathsSettingsRevision.actor_id == actor.id, PathsSettingsRevision.request_key == payload.idempotency_key))
            if prior:
                if prior.request_hash != digest:
                    raise HTTPException(409, {"code": "PATHS_REQUEST_CONFLICT"})
                return prior.response_snapshot
            current = current_paths(session, runtime)
            if current["version"] != payload.expected_version:
                raise HTTPException(409, {"code": "PATHS_VERSION_CHANGED"})
            try:
                paths = validate_paths(payload.model_dump(), runtime)
            except PathSettingsError as error:
                raise HTTPException(422, {"code": "PATHS_INVALID", "message": str(error)}) from None
            if paths["orders_root"] != current["orders_root"] and session.scalar(select(OrderFolderOperation.id).where(OrderFolderOperation.status.in_(["PENDING", "RUNNING", "RECONCILE"])).limit(1)):
                raise HTTPException(409, {"code": "ORDER_FOLDER_OPERATIONS_PENDING", "message": "Finish or reconcile pending order folder operations before changing their root."})
            result = {"version": current["version"] + 1, **paths}
            session.add(PathsSettingsRevision(version=result["version"], actor_id=actor.id, request_key=payload.idempotency_key, request_hash=digest, response_snapshot=result))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise HTTPException(409, {"code": "PATHS_VERSION_CHANGED"}) from None
            return result
    return router
