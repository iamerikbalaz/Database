"""Administrator-only source directory selection; never links material folders."""
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.material_review import _material
from app.auth.access import ADMIN, AccessDependency
from app.discovery_client import DiscoveryClient, DiscoveryClientError, validate_parent_path
from app.material_identity import require_material_idle


class DiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    parent_path: str = Field(max_length=2048)


def build_folder_discovery_router(database, client: DiscoveryClient):
    router = APIRouter(prefix="/api/materials", tags=["source folders"])

    def context(session, material_id, access):
        access.check(session, ADMIN)
        material = _material(session, material_id, access, lock=True)
        require_material_idle(session, material_id)
        return material.technical_identity, material.folder_path, material.updated_at

    @router.post("/{material_id}/folder-discovery")
    def discover(material_id: UUID, payload: DiscoveryRequest, access: AccessDependency):
        with database.session() as session:
            expected = context(session, material_id, access)
        try:
            validate_parent_path(payload.parent_path)
        except (ValueError, UnicodeError):
            raise HTTPException(422, {"code": "INVALID_FOLDER_PATH"}) from None
        result = None; failure = None
        try: result = client.listing(payload.parent_path)
        except DiscoveryClientError as exc: failure = exc.code
        with database.session() as session:
            if context(session, material_id, access) != expected:
                raise HTTPException(409, {"code": "DISCOVERY_MATERIAL_CHANGED"})
            if failure:
                status = 404 if failure == "MATERIAL_FOLDER_NOT_FOUND" else 409 if failure == "DISCOVERY_SOURCE_CHANGED" else 503 if failure in {
                    "DISCOVERY_UNAVAILABLE", "DISCOVERY_BUSY", "MATERIALS_ROOT_UNAVAILABLE"} else 422
                raise HTTPException(status, {"code": failure})
            assert result is not None
            return {"material_id": str(material_id), "technical_identity": expected[0], "parent_path": result.parent_path,
                "omitted_entries": result.omitted_entries, "directories": [
                    {**item.model_dump(), "identity_matches": item.name == expected[0]} for item in result.directories]}

    return router
