"""Validated bounded transport for one-level source directory discovery."""
import json
import unicodedata
from typing import Annotated, Protocol, Self
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_RESPONSE_BYTES = 4 * 1024**2
ERROR_CODES = frozenset({"DISCOVERY_BUSY", "DISCOVERY_TIME_LIMIT", "DISCOVERY_ENTRY_LIMIT", "DISCOVERY_DIRECTORY_LIMIT",
    "DISCOVERY_SOURCE_CHANGED", "DISCOVERY_READ_FAILED", "MATERIAL_FOLDER_NOT_FOUND", "MATERIALS_ROOT_UNAVAILABLE",
    "UNSAFE_MATERIAL_PATH", "SECURE_FILESYSTEM_ACCESS_UNAVAILABLE"})


class DiscoveryClientError(RuntimeError):
    def __init__(self, code="DISCOVERY_UNAVAILABLE"):
        self.code = code if code in ERROR_CODES else "DISCOVERY_UNAVAILABLE"
        super().__init__("Source folders could not be verified.")


def validate_parent_path(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or len(value.encode("utf-8", "surrogatepass")) > 2048:
        raise ValueError("Invalid source path")
    parts = tuple(value.split("/")) if value else ()
    if len(parts) > 16 or any(not part or part in {".", ".."} or len(part.encode("utf-8", "surrogatepass")) > 255
            or any(unicodedata.category(char).startswith("C") or char in "\\:" for char in part) for part in parts):
        raise ValueError("Invalid source path")
    return parts


class DiscoveryDirectory(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: Annotated[str, Field(min_length=1, max_length=255)]
    path: Annotated[str, Field(min_length=1, max_length=2048)]


class FolderDiscovery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Annotated[int, Field(ge=1, le=1)]
    parent_path: Annotated[str, Field(max_length=2048)]
    directories: Annotated[list[DiscoveryDirectory], Field(max_length=512)]
    omitted_entries: Annotated[int, Field(ge=0, le=4096)]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        parent = validate_parent_path(self.parent_path)
        names = set()
        for item in self.directories:
            if (validate_parent_path(item.path) != (*parent, item.name) or item.name in names
                    or len(validate_parent_path(item.name)) != 1):
                raise ValueError("Invalid source directory")
            names.add(item.name)
        if len(names) + self.omitted_entries > 4096:
            raise ValueError("Invalid entry count")
        return self


class DiscoveryClient(Protocol):
    def listing(self, parent_path: str) -> FolderDiscovery: ...


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError("Duplicate field")
        result[key] = value
    return result


class WorkerDiscoveryClient:
    def __init__(self, base_url: str):
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Invalid worker URL")
        self.base_url = base_url.rstrip("/")

    def listing(self, parent_path):
        try:
            validate_parent_path(parent_path)
            with httpx.stream("POST", self.base_url + "/internal/folder-discovery", json={"parent_path": parent_path},
                    timeout=httpx.Timeout(15, connect=5), follow_redirects=False, trust_env=False) as response:
                length = response.headers.get("Content-Length")
                if length is not None and not 0 <= int(length) <= MAX_RESPONSE_BYTES: raise ValueError()
                chunks = []; size = 0
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES: raise ValueError()
                    chunks.append(chunk)
                value = json.loads(b"".join(chunks), object_pairs_hook=_unique_object)
                if response.status_code != 200:
                    if response.status_code in {404, 409, 422, 503}:
                        code = value.get("detail", {}).get("code")
                        if isinstance(code, str): raise DiscoveryClientError(code)
                    raise DiscoveryClientError()
                result = FolderDiscovery.model_validate(value, strict=True)
                if result.parent_path != parent_path: raise ValueError()
                return result
        except DiscoveryClientError:
            raise
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError, ArithmeticError, RecursionError):
            raise DiscoveryClientError() from None
