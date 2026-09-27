"""Authenticated contents strictly beneath the material's linked source folder."""
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query
from app.api.material_review import _material
from app.auth.access import AccessDependency
from app.discovery_client import DiscoveryClientError, validate_parent_path
from app.material_identity import require_material_idle


def build_folder_contents_router(database, client):
    router = APIRouter(prefix="/api/materials", tags=["source folders"])

    def context(session, material_id, access):
        access.check(session)
        material = _material(session, material_id, access, historical=access.user.role == "ADMIN")
        require_material_idle(session, material_id)
        if not material.folder_path: raise HTTPException(409, {"code": "FOLDER_REQUIRED"})
        access.require_folder(material.folder_path, material.technical_identity)
        return material.folder_path, material.technical_identity, material.updated_at

    @router.get("/{material_id}/folder-contents")
    def contents(material_id: UUID, access: AccessDependency, path: str = Query(default="", max_length=2048)):
        try: validate_parent_path(path)
        except (ValueError, UnicodeError): raise HTTPException(422, {"code": "INVALID_FOLDER_PATH"}) from None
        with database.session() as session: expected = context(session, material_id, access)
        parent = expected[0] + ("/" + path if path else "")
        try: validate_parent_path(parent)
        except (ValueError, UnicodeError): raise HTTPException(422, {"code": "INVALID_FOLDER_PATH"}) from None
        result, failure = None, None
        try: result = client.listing(parent)
        except DiscoveryClientError as exc: failure = exc.code
        with database.session() as session:
            if context(session, material_id, access) != expected: raise HTTPException(409, {"code": "DISCOVERY_MATERIAL_CHANGED"})
            if failure:
                status = 404 if failure == "MATERIAL_FOLDER_NOT_FOUND" else 409 if failure == "DISCOVERY_SOURCE_CHANGED" else 503
                raise HTTPException(status, {"code": failure})
            return {"material_id": str(material_id), "path": path, "omitted_entries": result.omitted_entries,
                "entries": [{"name": item.name, "path": path + "/" + item.name if path else item.name,
                             "kind": item.kind, "size": item.size} for item in result.entries]}

    return router
