"""Internal read-only HTTP API for material preflight."""

import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.preflight import Finding, ZipPolicy
from app.secure_filesystem import (
    MaterialFolderNotFound,
    MaterialsRootUnavailable,
    SecureFilesystemAccessUnavailable,
    UnsafeMaterialPath,
    inspect_material_secure,
)
from app.source_metadata import SourceMetadataResult


SCHEMA_VERSION = 1
_FROM_ENVIRONMENT = object()
_METADATA_ERROR_CODES = frozenset({
    "SOURCE_METADATA_EMPTY",
    "SOURCE_METADATA_INVALID_FORMAT",
    "SOURCE_METADATA_TOO_LARGE",
    "SOURCE_METADATA_UNREADABLE",
    "SOURCE_METADATA_UNSAFE_FILE",
})
_BLOCKING_SECURITY_CODES = frozenset({
    "MATERIAL_INSPECTION_FAILED",
    "MATERIAL_PATH_INVALID",
    "SECURE_FILESYSTEM_ACCESS_UNAVAILABLE",
    "UNSAFE_RESOLUTION",
})


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MaterialPreflightRequest(StrictModel):
    folder_path: str


class FindingResponse(StrictModel):
    code: str
    path: str
    message: str


class MetadataResponse(StrictModel):
    status: Literal["MISSING", "VALID", "WARNING", "INVALID"]
    source_file_name: str | None
    sha256: str | None
    raw_content: str | None
    hex_color: str | None
    width_cm: str | None
    height_cm: str | None
    warnings: list[FindingResponse]
    errors: list[FindingResponse]


class MaterialPreflightResponse(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    folder_path: str
    folder_name: str
    master_resolution: str | None
    master_last_modified_at: str | None
    policy: ZipPolicy | None
    metadata: MetadataResponse
    warnings: list[FindingResponse]
    errors: list[FindingResponse]
    can_continue: bool


class HealthResponse(StrictModel):
    status: Literal["ok"] = "ok"
    service: Literal["worker"] = "worker"
    version: str = "0.1.0"


def _normalize_relative_path(value: str) -> tuple[str, tuple[str, ...]]:
    """Validate a portable relative path before making any filesystem call."""
    if not value or "\x00" in value:
        raise ValueError("folder_path must be a non-empty relative path")

    windows_path = PureWindowsPath(value)
    posix_value = value.replace("\\", "/")
    posix_path = PurePosixPath(posix_value)
    if (windows_path.drive or windows_path.root or posix_path.is_absolute()
            or value.startswith(("\\", "/"))):
        raise ValueError("Absolute paths are not allowed")

    parts = tuple(posix_value.split("/"))
    if any(part in {"", ".", ".."} or ":" in part for part in parts):
        raise ValueError("folder_path contains an invalid or traversal component")
    return "/".join(parts), parts


def _empty_metadata(metadata_status: Literal["MISSING", "INVALID"]) -> MetadataResponse:
    return MetadataResponse(
        status=metadata_status,
        source_file_name=None,
        sha256=None,
        raw_content=None,
        hex_color=None,
        width_cm=None,
        height_cm=None,
        warnings=[],
        errors=[],
    )


def _error_response(
    folder_path: str,
    folder_name: str,
    *,
    code: str,
    message: str,
    metadata_status: Literal["MISSING", "INVALID"],
) -> MaterialPreflightResponse:
    return MaterialPreflightResponse(
        folder_path=folder_path,
        folder_name=folder_name,
        master_resolution=None,
        master_last_modified_at=None,
        policy=None,
        metadata=_empty_metadata(metadata_status),
        warnings=[],
        errors=[FindingResponse(code=code, path=folder_path, message=message)],
        can_continue=False,
    )


def _safe_finding(finding: Finding, folder_path: str, *, metadata: bool) -> FindingResponse:
    path = f"{folder_path}/metadata.txt" if metadata else folder_path
    if finding.code == "NON_STANDARD_RESOLUTION":
        path = f"{folder_path}/{Path(finding.path).name}"

    messages = {
        "MATERIAL_INSPECTION_FAILED": "Material folder could not be inspected",
        "MATERIAL_PATH_INVALID": "Material folder path is invalid or unsafe",
        "NO_RESOLUTION": "No resolution directory found",
        "SECURE_FILESYSTEM_ACCESS_UNAVAILABLE": (
            "Secure descriptor-based filesystem access is unavailable"
        ),
        "UNSAFE_RESOLUTION": "A linked resolution directory is not allowed",
    }
    return FindingResponse(
        code=finding.code,
        path=path,
        message=messages.get(finding.code, finding.message),
    )


def _metadata_response(result: SourceMetadataResult, folder_path: str) -> MetadataResponse:
    findings = [_safe_finding(item, folder_path, metadata=True) for item in result.warnings]
    errors = [item for item in findings if item.code in _METADATA_ERROR_CODES]
    warnings = [item for item in findings if item.code not in _METADATA_ERROR_CODES]
    return MetadataResponse(
        status="INVALID" if result.status == "NOT_SCANNED" else result.status,
        source_file_name=None if result.status in {"NOT_SCANNED", "MISSING"} else result.source_filename,
        sha256=result.sha256,
        raw_content=result.raw_content,
        hex_color=result.hex_color,
        width_cm=str(result.width_cm) if result.width_cm is not None else None,
        height_cm=str(result.height_cm) if result.height_cm is not None else None,
        warnings=warnings,
        errors=errors,
    )


def _configured_root(value: str | os.PathLike[str] | None) -> Path:
    if value is None or not str(value).strip() or "\x00" in str(value):
        raise RuntimeError("MATERIALS_ROOT is not configured")
    path = Path(value)
    if not path.is_absolute():
        raise RuntimeError("MATERIALS_ROOT must be an absolute directory")
    return path


def _perform_preflight(
    request: MaterialPreflightRequest,
    configured_root: str | os.PathLike[str] | None,
) -> tuple[MaterialPreflightResponse, int]:
    try:
        folder_path, parts = _normalize_relative_path(request.folder_path)
    except ValueError as exc:
        return _error_response(
            "", "", code="INVALID_FOLDER_PATH", message=str(exc), metadata_status="INVALID"
        ), status.HTTP_422_UNPROCESSABLE_CONTENT

    folder_name = parts[-1]
    try:
        root = _configured_root(configured_root)
    except RuntimeError:
        return _error_response(
            folder_path,
            folder_name,
            code="MATERIALS_ROOT_UNAVAILABLE",
            message="MATERIALS_ROOT is not configured or unavailable",
            metadata_status="INVALID",
        ), status.HTTP_503_SERVICE_UNAVAILABLE

    try:
        result = inspect_material_secure(root, parts)
    except MaterialFolderNotFound:
        return _error_response(
            folder_path,
            folder_name,
            code="MATERIAL_FOLDER_NOT_FOUND",
            message="Material folder does not exist",
            metadata_status="MISSING",
        ), status.HTTP_404_NOT_FOUND
    except MaterialsRootUnavailable:
        return _error_response(
            folder_path,
            folder_name,
            code="MATERIALS_ROOT_UNAVAILABLE",
            message="MATERIALS_ROOT is not configured or unavailable",
            metadata_status="INVALID",
        ), status.HTTP_503_SERVICE_UNAVAILABLE
    except SecureFilesystemAccessUnavailable:
        return _error_response(
            folder_path,
            folder_name,
            code="SECURE_FILESYSTEM_ACCESS_UNAVAILABLE",
            message="Secure descriptor-based filesystem access is unavailable",
            metadata_status="INVALID",
        ), status.HTTP_422_UNPROCESSABLE_CONTENT
    except UnsafeMaterialPath as exc:
        return _error_response(
            folder_path,
            folder_name,
            code="UNSAFE_MATERIAL_PATH",
            message=str(exc),
            metadata_status="INVALID",
        ), status.HTTP_422_UNPROCESSABLE_CONTENT

    warnings = [_safe_finding(item, folder_path, metadata=False)
                for item in result.master_warnings]
    blocking = [*result.errors, *result.master_errors]
    errors = [_safe_finding(item, folder_path, metadata=False) for item in blocking]
    response_status = (
        status.HTTP_422_UNPROCESSABLE_CONTENT
        if any(item.code in _BLOCKING_SECURITY_CODES for item in blocking)
        else status.HTTP_200_OK
    )
    return MaterialPreflightResponse(
        folder_path=folder_path,
        folder_name=folder_name,
        master_resolution=result.master_resolution,
        master_last_modified_at=result.master_modified_at,
        policy=result.selected_zip_policy,
        metadata=_metadata_response(result, folder_path),
        warnings=warnings,
        errors=errors,
        can_continue=not errors,
    ), response_status


def create_app(
    materials_root: str | os.PathLike[str] | None | object = _FROM_ENVIRONMENT,
) -> FastAPI:
    configured_root = (
        os.getenv("MATERIALS_ROOT") if materials_root is _FROM_ENVIRONMENT else materials_root
    )
    application = FastAPI(title="REAWOTE Worker Internal API", version="0.1.0")

    @application.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, __: RequestValidationError) -> JSONResponse:
        result = _error_response(
            "",
            "",
            code="INVALID_REQUEST",
            message="Request body must contain one string folder_path",
            metadata_status="INVALID",
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=result.model_dump(mode="json"),
        )

    @application.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse()

    @application.post(
        "/internal/material-preflight",
        response_model=MaterialPreflightResponse,
        responses={
            404: {"model": MaterialPreflightResponse},
            422: {"model": MaterialPreflightResponse},
            503: {"model": MaterialPreflightResponse},
        },
        tags=["internal"],
    )
    def material_preflight(
        request: MaterialPreflightRequest,
        response: Response,
    ) -> MaterialPreflightResponse:
        result, response.status_code = _perform_preflight(request, configured_root)
        return result

    return application


app = create_app()
