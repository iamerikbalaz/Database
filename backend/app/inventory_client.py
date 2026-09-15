"""Strict, bounded transport for the internal content inventory contract."""
import hashlib
import json
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal, Protocol, Self
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas import MaterialZipPolicy
from app.worker_client import FolderName, FolderPath, MasterResolution, Sha256, WorkerClientError

MAX_INVENTORY_RESPONSE_BYTES = 64 * 1024 * 1024
INVENTORY_CODES = frozenset({
    "INVALID_FOLDER_PATH", "MATERIAL_FOLDER_NOT_FOUND", "MATERIALS_ROOT_UNAVAILABLE",
    "SECURE_FILESYSTEM_ACCESS_UNAVAILABLE", "UNSAFE_MATERIAL_PATH",
    "INVENTORY_TIME_LIMIT", "INVENTORY_DEPTH_LIMIT", "INVENTORY_ENTRY_LIMIT",
    "INVENTORY_UNSAFE_NAME", "INVENTORY_PATH_LIMIT", "INVENTORY_UNSAFE_ENTRY",
    "INVENTORY_SOURCE_CHANGED", "INVENTORY_FILE_LIMIT", "INVENTORY_TOTAL_LIMIT",
    "INVENTORY_READ_FAILED",
})


class InventoryClientError(WorkerClientError):
    def __init__(self, code: str = "INVENTORY_UNAVAILABLE"):
        self.code = code if code in INVENTORY_CODES else "INVENTORY_UNAVAILABLE"
        super().__init__("Source inventory could not be verified.")


def validate_relative_path(path: str):
    if (path.startswith("/") or any(part in {"", ".", ".."} for part in path.split("/"))
            or any(ord(char) < 32 or ord(char) == 127 or char in "\\:" for char in path)):
        raise ValueError("Invalid inventory path")


class InventoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: FolderPath
    kind: Literal["file", "directory"]
    size: Annotated[int, Field(ge=0, le=64 * 1024**3)]
    sha256: Sha256 | None

    @model_validator(mode="after")
    def check(self) -> Self:
        validate_relative_path(self.path)
        if (self.kind == "file") != (self.sha256 is not None) or (self.kind == "directory" and self.size != 0):
            raise ValueError("Invalid inventory entry")
        return self


class SourceInventory(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1]
    folder_name: FolderName
    master_resolution: MasterResolution | None
    master_last_modified_at: str | None
    policy: MaterialZipPolicy | None
    entries: Annotated[list[InventoryEntry], Field(max_length=20_000)]
    source_revision_hash: Sha256
    total_bytes: Annotated[int, Field(ge=0, le=256 * 1024**3)]

    @model_validator(mode="after")
    def check(self) -> Self:
        validate_relative_path(self.folder_name)
        if "/" in self.folder_name:
            raise ValueError("Inventory identity must be a basename")
        paths = [entry.path for entry in self.entries]
        if paths != sorted(set(paths)):
            raise ValueError("Inventory paths must be unique and sorted")
        directories = {entry.path for entry in self.entries if entry.kind == "directory"}
        resolutions = sorted((path for path in directories if re.fullmatch(r"[1-9][0-9]*K", path)), key=lambda path: (len(path), path))
        if self.master_resolution != (resolutions[-1] if resolutions else None):
            raise ValueError("Inventory must identify the highest root resolution")
        for path in paths:
            parent = str(PurePosixPath(path).parent)
            if parent != "." and parent not in directories:
                raise ValueError("Inventory is missing a parent directory")
        if sum(entry.size for entry in self.entries) != self.total_bytes:
            raise ValueError("Inventory total does not match entries")
        if self.master_resolution is None:
            if self.policy is not None or self.master_last_modified_at is not None:
                raise ValueError("Inventory master information is inconsistent")
        elif self.master_resolution not in directories or self.policy is None or self.master_last_modified_at is None:
            raise ValueError("Inventory master information is incomplete")
        else:
            if datetime.fromisoformat(self.master_last_modified_at).tzinfo is None:
                raise ValueError("Inventory timestamp must include its offset")
        canonical = self.model_dump(mode="json", include={
            "schema_version", "folder_name", "master_resolution", "policy", "entries",
        })
        digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        if digest != self.source_revision_hash:
            raise ValueError("Inventory hash is inconsistent")
        return self


class InventoryClient(Protocol):
    def inventory(self, folder_path: str) -> SourceInventory: ...


class WorkerInventoryClient:
    def __init__(self, base_url: str, timeout_seconds: float = 125):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or "\\" in base_url):
            raise ValueError("Invalid inventory worker base URL")
        if not 0 < timeout_seconds <= 300:
            raise ValueError("Inventory timeout must be positive and at most 300 seconds")
        self.url = base_url.rstrip("/") + "/internal/material-inventory"
        self.timeout = httpx.Timeout(timeout_seconds, connect=min(5, timeout_seconds))

    def read_content(self, folder_path: str) -> bytes:
        try:
            validate_relative_path(folder_path)
            if len(folder_path) > 2048:
                raise ValueError("Invalid inventory path")
            with httpx.stream("POST", self.url, json={"folder_path": folder_path},
                              timeout=self.timeout, follow_redirects=False, trust_env=False) as response:
                size = response.headers.get("Content-Length")
                if size is not None and not 0 <= int(size) <= MAX_INVENTORY_RESPONSE_BYTES:
                    raise ValueError("Inventory response limit")
                chunks = []; received = 0
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    received += len(chunk)
                    if received > MAX_INVENTORY_RESPONSE_BYTES:
                        raise ValueError("Inventory response limit")
                    chunks.append(chunk)
                content = b"".join(chunks)
                if response.status_code != 200:
                    if response.status_code in {404, 422, 503}:
                        body = json.loads(content)
                        code = body.get("detail", {}).get("code")
                        if isinstance(code, str) and code in INVENTORY_CODES:
                            raise InventoryClientError(code)
                    raise InventoryClientError()
            return content
        except InventoryClientError:
            raise
        except (httpx.HTTPError, ValueError, TypeError, AttributeError, ArithmeticError):
            raise InventoryClientError() from None

    def inventory(self, folder_path: str) -> SourceInventory:
        try:
            result = SourceInventory.model_validate_json(self.read_content(folder_path), strict=True)
            if result.folder_name != folder_path.rsplit("/", 1)[-1]:
                raise ValueError("Inventory identity does not match request")
            return result
        except InventoryClientError:
            raise
        except (httpx.HTTPError, ValueError, TypeError, AttributeError, ArithmeticError):
            # Never chain parser details; an untrusted response may contain secrets.
            raise InventoryClientError() from None
