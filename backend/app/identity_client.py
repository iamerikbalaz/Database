"""Strict private-worker identity protocol. Responses never contain source bytes."""
import json
from typing import Annotated, Literal, Protocol, Self
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.inventory_client import INVENTORY_CODES, WorkerInventoryClient, validate_relative_path
from app.material_review import canonical_hash
from app.schemas import FolderPath, Sha256

IDENTITY_CODES = INVENTORY_CODES | frozenset({
    "IDENTITY_CROSS_DEVICE_UNSUPPORTED", "IDENTITY_PARENT_ENTRY_LIMIT", "IDENTITY_PATH_LIMIT",
    "IDENTITY_SOURCE_UNSUPPORTED", "IDENTITY_TARGET_COLLISION", "IDENTITY_TARGET_INSIDE_SOURCE", "IDENTITY_TARGET_INVALID",
    "METADATA_REWRITE_UNSUPPORTED", "METADATA_UNMAPPED_REFERENCE", "SOURCE_METADATA_MISSING",
    "SOURCE_METADATA_UNSAFE_FILE", "SOURCE_METADATA_UNREADABLE", "SOURCE_METADATA_TOO_LARGE",
    "IDENTITY_ENTRY_CHANGED", "IDENTITY_METADATA_CHANGED", "IDENTITY_OPERATION_FAILED", "IDENTITY_OUTPUT_CHANGED",
    "IDENTITY_PLAN_BLOCKED", "IDENTITY_PLAN_CHANGED", "IDENTITY_PREPARATION_INTERRUPTED", "IDENTITY_PREPARATION_REJECTED",
    "IDENTITY_RECOVERY_REQUIRED", "JOURNAL_BUSY", "JOURNAL_INVALID_STATE", "JOURNAL_REQUEST_CONFLICT",
    "JOURNAL_ROOT_OVERLAPS_SOURCE", "JOURNAL_TARGET_EXISTS", "JOURNAL_WRITE_FAILED", "JOURNAL_BACKUP_FAILED",
    "JOURNAL_ID_INVALID", "JOURNAL_NAME_INVALID", "JOURNAL_PLATFORM_UNSUPPORTED", "JOURNAL_RENAME_FAILED",
    "JOURNAL_SIZE_LIMIT", "JOURNAL_UNSAFE_STORAGE", "SOURCE_MUTATIONS_DISABLED", "IDENTITY_UNAVAILABLE",
})
MAX_IDENTITY_RESPONSE_BYTES = 32 * 1024 * 1024
METADATA_FIELDS = {"FOLDER", "MANUFACTURER", "PRODUCT_NAME", "CATEGORY", "PRODUCT_NUMBER", "BASE_NAME",
                   "TEXTURE_SIZE_SOURCE", "COLOR.measured_from", "SOURCE.SBS"}


class IdentityClientError(RuntimeError):
    def __init__(self, code="IDENTITY_UNAVAILABLE"):
        self.code = code if code in IDENTITY_CODES else "IDENTITY_UNAVAILABLE"
        super().__init__("Identity operation could not be verified.")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class IdentityFinding(StrictModel):
    code: Annotated[str, Field(max_length=100)]
    path: FolderPath

    @model_validator(mode="after")
    def check(self) -> Self:
        if self.code not in IDENTITY_CODES: raise ValueError("Unknown identity finding")
        validate_relative_path(self.path)
        return self


class IdentityChange(StrictModel):
    kind: Literal["file", "directory"]
    source: FolderPath
    target: FolderPath
    sha256: Sha256 | None

    @model_validator(mode="after")
    def check(self) -> Self:
        validate_relative_path(self.source); validate_relative_path(self.target)
        if self.source == self.target or (self.kind == "file") != (self.sha256 is not None):
            raise ValueError("Invalid rename")
        return self


class IdentityMetadata(StrictModel):
    before_hash: Sha256 | None
    after_hash: Sha256 | None
    changed_fields: Annotated[list[str], Field(max_length=9)]

    @model_validator(mode="after")
    def check(self) -> Self:
        if self.changed_fields != sorted(set(self.changed_fields)) or set(self.changed_fields) - METADATA_FIELDS:
            raise ValueError("Invalid metadata field list")
        if self.changed_fields and (self.before_hash is None or self.after_hash is None or self.before_hash == self.after_hash):
            raise ValueError("Missing metadata proof")
        return self


class IdentityPlan(StrictModel):
    schema_version: Literal[1]
    planner_version: Literal["identity-plan-1"]
    source_path: FolderPath
    target_path: FolderPath
    source_revision_hash: Sha256
    changes: Annotated[list[IdentityChange], Field(max_length=20_000)]
    metadata: IdentityMetadata
    errors: Annotated[list[IdentityFinding], Field(max_length=40_010)]
    warnings: Annotated[list[IdentityFinding], Field(max_length=10)]
    ready: bool
    plan_hash: Sha256

    @model_validator(mode="after")
    def check(self) -> Self:
        validate_relative_path(self.source_path); validate_relative_path(self.target_path)
        if self.ready != (not self.errors) or self.source_path == self.target_path:
            raise ValueError("Invalid plan outcome")
        sources = [item.source for item in self.changes]
        if sources != sorted(set(sources)): raise ValueError("Duplicate or unsorted renames")
        old = self.source_path.rsplit("/", 1)[-1]; new = self.target_path.rsplit("/", 1)[-1]
        for item in self.changes:
            mapped = "/".join(new + part[len(old):] if part == old or part.startswith((old + "_", old + ".")) else part
                              for part in item.source.split("/"))
            if item.target != mapped: raise ValueError("Unexpected rename")
        if self.ready and self.metadata.before_hash is not None:
            if self.metadata.after_hash is None or bool(self.metadata.changed_fields) != (self.metadata.before_hash != self.metadata.after_hash):
                raise ValueError("Incomplete metadata proof")
        return self

    def verify_request(self, request):
        if (self.source_path, self.target_path) != (request["folder_path"], request["target_path"]):
            raise ValueError("Unexpected identity target")
        digest = canonical_hash({"plan": self.model_dump(mode="json", exclude={"plan_hash"}),
                                 "brand_name": request["brand_name"], "material_name": request["material_name"]})
        if self.plan_hash != digest: raise ValueError("Identity plan hash mismatch")


class IdentityResult(StrictModel):
    operation_id: UUID
    status: Literal["COMPLETED", "ROLLED_BACK", "RECOVERY_REQUIRED", "REJECTED"]
    plan_hash: Sha256
    source_path: FolderPath
    target_path: FolderPath
    source_revision_hash: Sha256 | None
    target_revision_hash: Sha256 | None
    failure_code: str | None

    @model_validator(mode="after")
    def check(self) -> Self:
        if self.failure_code is not None and self.failure_code not in IDENTITY_CODES:
            raise ValueError("Unknown operation finding")
        if (self.status == "COMPLETED") != (self.target_revision_hash is not None and self.failure_code is None):
            raise ValueError("Contradictory operation result")
        if self.status != "COMPLETED" and (self.target_revision_hash is not None or self.failure_code is None):
            raise ValueError("Unexpected failed operation result")
        if (self.status == "REJECTED") != (self.source_revision_hash is None):
            raise ValueError("Missing source proof")
        return self


class IdentityClient(Protocol):
    def plan(self, request: dict) -> IdentityPlan: ...
    def execute(self, request: dict) -> IdentityResult: ...


class WorkerIdentityClient(WorkerInventoryClient):
    def __init__(self, base_url, token=None, enabled=False, timeout_seconds=300):
        super().__init__(base_url, timeout_seconds)
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.enabled = enabled

    def _request(self, path, payload, *, mutation=False):
        if mutation and (not self.enabled or not self._token): raise IdentityClientError("SOURCE_MUTATIONS_DISABLED")
        headers = {"Authorization": "Bearer " + self._token.get_secret_value()} if mutation else {}
        try:
            with httpx.stream("POST", self.base_url + path, json=payload, headers=headers,
                              timeout=self.timeout, trust_env=False, follow_redirects=False) as response:
                declared = response.headers.get("Content-Length")
                if declared is not None and not 0 <= int(declared) <= MAX_IDENTITY_RESPONSE_BYTES: raise ValueError()
                chunks = []; size = 0
                for chunk in response.iter_bytes(chunk_size=65536):
                    size += len(chunk)
                    if size > MAX_IDENTITY_RESPONSE_BYTES: raise ValueError()
                    chunks.append(chunk)
                content = b"".join(chunks)
                if response.status_code != 200:
                    if response.status_code in {401, 409, 422, 503}:
                        body = json.loads(content)
                        raise IdentityClientError(body.get("detail", {}).get("code"))
                    raise IdentityClientError()
                return content
        except IdentityClientError: raise
        except (httpx.HTTPError, ValueError, TypeError, AttributeError, ArithmeticError):
            raise IdentityClientError() from None

    def plan(self, request):
        try:
            result = IdentityPlan.model_validate_json(self._request("/internal/material-identity-plan", request))
            result.verify_request(request)
            return result
        except IdentityClientError: raise
        except (ValueError, TypeError, AttributeError, ArithmeticError): raise IdentityClientError() from None

    def execute(self, request):
        try:
            result = IdentityResult.model_validate_json(self._request("/internal/material-identity-execute", request, mutation=True))
            if (str(result.operation_id), result.plan_hash, result.source_path, result.target_path) != (
                    request["operation_id"], request["expected_plan_hash"], request["folder_path"], request["target_path"]):
                raise ValueError("Unexpected operation result")
            return result
        except IdentityClientError: raise
        except (ValueError, TypeError, AttributeError, ArithmeticError): raise IdentityClientError() from None
