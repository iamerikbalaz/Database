"""Strict bounded transport for derived preview images; never returns originals."""
import base64
import hashlib
import json
import re
from typing import Annotated, Literal, Protocol, Self
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.inventory_client import validate_relative_path
from app.worker_client import FolderName, Sha256

MAX_RESPONSE_BYTES = 3 * 1024**2
MAX_IMAGE_BYTES = 2 * 1024**2
ERROR_CODES = frozenset({"PREVIEW_BUSY", "PREVIEW_UNSAFE_ENTRY", "PREVIEW_UNSAFE_NAME", "PREVIEW_SOURCE_CHANGED",
    "PREVIEW_FILE_LIMIT", "PREVIEW_TOTAL_LIMIT", "PREVIEW_ENTRY_LIMIT", "PREVIEW_TIME_LIMIT", "PREVIEW_READ_FAILED",
    "PREVIEW_PIXEL_LIMIT", "PREVIEW_MULTIFRAME_UNSUPPORTED", "PREVIEW_MODE_UNSUPPORTED", "PREVIEW_OUTPUT_LIMIT",
    "PREVIEW_DECODER_UNAVAILABLE", "PREVIEW_RESOURCE_LIMIT", "PREVIEW_UNREADABLE", "PREVIEW_DECODER_FAILED",
    "PREVIEW_TIMEOUT", "PREVIEW_NOT_FOUND", "PREVIEW_EXTENSION_MISMATCH", "MATERIAL_FOLDER_NOT_FOUND",
    "MATERIALS_ROOT_UNAVAILABLE", "SECURE_FILESYSTEM_ACCESS_UNAVAILABLE", "UNSAFE_MATERIAL_PATH"})


class PreviewClientError(RuntimeError):
    def __init__(self, code="PREVIEW_UNAVAILABLE"):
        self.code = code if code in ERROR_CODES else "PREVIEW_UNAVAILABLE"
        super().__init__("Material preview could not be verified.")


def validate_preview_name(name):
    validate_relative_path(name)
    if "/" in name or "." not in name or len(name.encode("utf-8")) > 255 or name.rsplit(".", 1)[-1].lower() not in {"jpg", "jpeg", "png", "tif", "tiff", "webp"}:
        raise ValueError("Invalid preview name")


class PreviewEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: FolderName
    size: Annotated[int, Field(ge=1, le=64 * 1024**2)]
    sha256: Sha256

    @model_validator(mode="after")
    def check(self) -> Self:
        validate_preview_name(self.name)
        return self


class PreviewListing(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1]
    folder_name: FolderName
    missing: bool
    ignored_entries: Annotated[int, Field(ge=0, le=512)]
    items: Annotated[list[PreviewEntry], Field(max_length=64)]

    @model_validator(mode="after")
    def check(self) -> Self:
        validate_relative_path(self.folder_name)
        if ("/" in self.folder_name or len({item.name for item in self.items}) != len(self.items)
                or (self.missing and (self.items or self.ignored_entries))
                or len(self.items) + self.ignored_entries > 512 or sum(item.size for item in self.items) > 512 * 1024**2):
            raise ValueError("Inconsistent preview listing")
        return self


class PreviewImage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1]
    folder_name: FolderName
    name: FolderName
    source_sha256: Sha256
    source_format: Literal["JPEG", "PNG", "TIFF", "WEBP"]
    width: Annotated[int, Field(ge=1, le=1024)]
    height: Annotated[int, Field(ge=1, le=1024)]
    media_type: Literal["image/jpeg"]
    sha256: Sha256
    data: Annotated[str, Field(max_length=2800000, repr=False)]

    @model_validator(mode="after")
    def check(self) -> Self:
        validate_preview_name(self.name); validate_relative_path(self.folder_name)
        if "/" in self.folder_name: raise ValueError("Invalid preview folder")
        data = self.image_bytes()
        if (not 0 < len(data) <= MAX_IMAGE_BYTES or not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9")
                or hashlib.sha256(data).hexdigest() != self.sha256):
            raise ValueError("Invalid derived preview")
        return self

    def image_bytes(self):
        return base64.b64decode(self.data, validate=True)


class PreviewClient(Protocol):
    def listing(self, folder_path: str) -> PreviewListing: ...
    def image(self, folder_path: str, name: str, expected_sha256: str) -> PreviewImage: ...


class WorkerPreviewClient:
    def __init__(self, base_url: str):
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Invalid worker URL")
        self.base_url = base_url.rstrip("/")

    def _read(self, suffix, payload):
        try:
            validate_relative_path(payload["folder_path"])
            if len(payload["folder_path"]) > 2048: raise ValueError()
            with httpx.stream("POST", self.base_url + suffix, json=payload, timeout=httpx.Timeout(30, connect=5),
                              follow_redirects=False, trust_env=False) as response:
                length = response.headers.get("Content-Length")
                if length is not None and not 0 <= int(length) <= MAX_RESPONSE_BYTES: raise ValueError()
                chunks = []; size = 0
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES: raise ValueError()
                    chunks.append(chunk)
                content = b"".join(chunks)
                if response.status_code != 200:
                    if response.status_code in {404, 409, 422, 503}:
                        code = json.loads(content).get("detail", {}).get("code")
                        if isinstance(code, str): raise PreviewClientError(code)
                    raise PreviewClientError()
                return content
        except PreviewClientError:
            raise
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
            raise PreviewClientError() from None

    def listing(self, folder_path):
        try:
            result = PreviewListing.model_validate_json(self._read("/internal/material-previews", {"folder_path": folder_path}), strict=True)
            if result.folder_name != folder_path.rsplit("/", 1)[-1]: raise ValueError()
            return result
        except PreviewClientError:
            raise
        except (ValueError, TypeError, AttributeError, ArithmeticError):
            raise PreviewClientError() from None

    def image(self, folder_path, name, expected_sha256):
        try:
            validate_preview_name(name)
            if not isinstance(expected_sha256, str) or re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None: raise ValueError()
            result = PreviewImage.model_validate_json(self._read("/internal/material-preview", {
                "folder_path": folder_path, "name": name, "expected_sha256": expected_sha256}), strict=True)
            if result.folder_name != folder_path.rsplit("/", 1)[-1] or result.name != name or result.source_sha256 != expected_sha256:
                raise ValueError()
            return result
        except PreviewClientError:
            raise
        except (ValueError, TypeError, AttributeError, ArithmeticError):
            raise PreviewClientError() from None
