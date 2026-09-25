"""Authenticated previews, reauthorized after source IO before disclosing pixels."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response

from app.api.material_review import _material
from app.auth.access import AccessDependency
from app.material_identity import require_material_idle
from app.preview_client import PreviewClient, PreviewClientError, validate_preview_name
from app.worker_client import Sha256


def build_material_previews_router(database, client: PreviewClient):
    router = APIRouter(prefix="/api/materials", tags=["material previews"])

    def read(material_id, access, operation):
        with database.session() as session:
            access.check(session)
            material = _material(session, material_id, access)
            require_material_idle(session, material_id)
            if not material.folder_path: raise HTTPException(409, {"code": "PREVIEW_FOLDER_REQUIRED"})
            access.require_folder(material.folder_path, material.technical_identity)
            context = (material.folder_path, material.technical_identity)
        result = None; failure = None
        try: result = operation(context[0])
        except PreviewClientError as exc: failure = exc.code
        # No source data or dependency diagnostics escape before renewed access
        # and material checks, even when the worker returned an error.
        with database.session() as session:
            access.check(session)
            material = _material(session, material_id, access, lock=True)
            require_material_idle(session, material_id)
            if (material.folder_path, material.technical_identity) != context:
                raise HTTPException(409, {"code": "PREVIEW_MATERIAL_CHANGED"})
            if failure:
                code = 404 if failure in {"PREVIEW_NOT_FOUND", "MATERIAL_FOLDER_NOT_FOUND"} else 409 if failure == "PREVIEW_SOURCE_CHANGED" else 503 if failure in {
                    "PREVIEW_UNAVAILABLE", "PREVIEW_BUSY", "PREVIEW_TIMEOUT", "PREVIEW_DECODER_UNAVAILABLE", "MATERIALS_ROOT_UNAVAILABLE"} else 422
                raise HTTPException(code, {"code": failure})
            return result

    @router.get("/{material_id}/previews")
    def listing(material_id: UUID, access: AccessDependency):
        result = read(material_id, access, client.listing)
        return {"material_id": str(material_id), "missing": result.missing,
            "ignored_entries": result.ignored_entries, "items": [item.model_dump(mode="json") for item in result.items]}

    @router.get("/{material_id}/preview")
    def image(material_id: UUID, access: AccessDependency, name: Annotated[str, Query(min_length=1, max_length=255)], expected_sha256: Sha256,
              size: Annotated[int, Query()] = 1024):
        if size not in {256, 512, 1024}: raise HTTPException(422, {"code": "PREVIEW_SIZE_UNSUPPORTED"})
        try: validate_preview_name(name)
        except (ValueError, UnicodeError): raise HTTPException(422, {"code": "PREVIEW_UNSAFE_NAME"}) from None
        result = read(material_id, access, lambda folder: client.image(folder, name, expected_sha256) if size == 1024
                      else client.image(folder, name, expected_sha256, size))
        return Response(result.image_bytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff", "Content-Disposition": 'inline; filename="preview.jpg"',
            "X-Preview-Width": str(result.width), "X-Preview-Height": str(result.height), "X-Preview-Sha256": result.sha256})

    return router
