from typing import Annotated, Literal
from uuid import UUID
from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.access import ADMIN, CATALOG_MANAGERS, AccessDependency
from app.db.path_settings_models import PathsSettingsRevision
from app.db.notion_sync_models import OrderFolderOperation
from app.material_review import canonical_hash
from app.path_settings import current_paths, list_sbs_templates, lock_paths, PathSettingsError, validate_paths, checked_directory, paths_snapshot
from app.local_filesystem import LocalFilesError
from app.schemas import ApiSchema

DirectoryPath = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]


class PathsSettingsUpdate(ApiSchema):
    idempotency_key: UUID
    expected_version: Annotated[int, Field(strict=True, ge=0)]
    sbs_templates_root: DirectoryPath
    orders_root: DirectoryPath
    materials_root: DirectoryPath
    published_library_root: DirectoryPath | None = None


class PathsFolderSelection(ApiSchema):
    field: Literal["sbs_templates_root", "orders_root", "materials_root", "published_library_root"]


def build_path_settings_router(database, runtime, library=None):
    router = APIRouter(prefix="/api/settings/paths", tags=["settings"])

    def local_client(request):
        return request.client is not None and request.client.host in {"127.0.0.1", "::1", "testclient"}

    def picker_available():
        return library is not None and callable(getattr(library, "select_settings_folder", None))

    def view(snapshot, request):
        return {**paths_snapshot(snapshot, runtime), "can_select_folder": picker_available() and local_client(request)}

    @router.get("")
    def current(access: AccessDependency, request: Request):
        with database.session() as session:
            access.check(session, ADMIN)
            return view(current_paths(session, runtime), request)

    @router.post("/select-folder")
    def select_folder(payload: PathsFolderSelection, access: AccessDependency, request: Request):
        with database.session() as session:
            access.check(session, ADMIN)
            initial = current_paths(session, runtime)[payload.field]
        if not local_client(request): raise HTTPException(403, {"code": "LOCAL_DESKTOP_ONLY"})
        if not picker_available(): raise HTTPException(503, {"code": "LOCAL_DESKTOP_UNAVAILABLE"})
        descriptions = {"sbs_templates_root": "Choose the SBS templates folder", "orders_root": "Choose the root for new orders",
            "materials_root": "Choose the customers and materials root inside Test_data", "published_library_root": "Choose the published materials library folder"}
        selected = None; failure = None
        try:
            selected = library.select_settings_folder(initial, descriptions[payload.field])
        except (LocalFilesError, OSError, ValueError) as error:
            failure = error.code if isinstance(error, LocalFilesError) and error.code in {"LOCAL_PICKER_BUSY", "LOCAL_PICKER_TIMEOUT"} else "LOCAL_PICKER_UNAVAILABLE"
        with database.session() as session:
            access.check(session, ADMIN)  # The native dialog may remain open for minutes.
        if failure: raise HTTPException(503, {"code": failure})
        if selected is not None:
            try:
                path = checked_directory(selected)
                if payload.field == "materials_root" and not path.is_relative_to(checked_directory(runtime.materials_root)):
                    raise PathSettingsError("Material data must stay inside the configured Test_data root during testing.")
                selected = str(path)
            except PathSettingsError as error:
                raise HTTPException(422, {"code": "PATHS_INVALID", "message": str(error)}) from None
        # Merely populate an input. No settings revision or filesystem grant is created.
        return {"folder_path": selected}

    @router.get("/sbs-templates")
    def templates(access: AccessDependency):
        with database.session() as session:
            access.check(session, CATALOG_MANAGERS)
            try:
                return {"items": list_sbs_templates(current_paths(session, runtime)["sbs_templates_root"])}
            except PathSettingsError as error:
                raise HTTPException(503, {"code": "SBS_TEMPLATES_UNAVAILABLE", "message": str(error)}) from None

    @router.post("")
    def update(payload: PathsSettingsUpdate, access: AccessDependency, request: Request):
        digest = canonical_hash(payload.model_dump(mode="json", exclude_none=True))
        with database.session() as session:
            actor = access.check(session, ADMIN)
            lock_paths(session, exclusive=True)
            actor = access.check(session, ADMIN)
            prior = session.scalar(select(PathsSettingsRevision).where(PathsSettingsRevision.actor_id == actor.id, PathsSettingsRevision.request_key == payload.idempotency_key))
            if prior:
                if prior.request_hash != digest:
                    raise HTTPException(409, {"code": "PATHS_REQUEST_CONFLICT"})
                return view(prior.response_snapshot, request)
            current = current_paths(session, runtime)
            if current["version"] != payload.expected_version:
                raise HTTPException(409, {"code": "PATHS_VERSION_CHANGED"})
            try:
                paths = validate_paths({**payload.model_dump(), "published_library_root": payload.published_library_root or current["published_library_root"]}, runtime)
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
            return view(result, request)
    return router
