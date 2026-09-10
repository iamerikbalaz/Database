from decimal import Decimal
from typing import Annotated, Literal, Protocol, Self

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from app.db.models import MaterialMetadataStatus
from app.schemas import MaterialZipPolicy


MAX_WORKER_RESPONSE_BYTES = 5 * 1024 * 1024
WORKER_RESPONSE_CHUNK_BYTES = 64 * 1024

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
FolderName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=255),
]
SourceFilename = Annotated[str, StringConstraints(min_length=1, max_length=255)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9A-F]{6}$")]
FolderPath = Annotated[str, StringConstraints(min_length=1, max_length=2048)]
MasterResolution = Annotated[
    str,
    StringConstraints(min_length=2, max_length=16, pattern=r"^[1-9][0-9]*K$"),
]


class WorkerClientError(RuntimeError):
    """A controlled worker dependency failure safe to expose without payload data."""


class WorkerUnavailableError(WorkerClientError):
    pass


class WorkerResponseError(WorkerClientError):
    pass


class WorkerFindingResponse(BaseModel):
    """Exact finding object returned inside the worker wire response."""

    model_config = ConfigDict(extra="forbid", strict=True)

    code: str
    message: str
    path: str

    def to_internal(self) -> "WorkerFinding":
        return WorkerFinding(code=self.code, message=self.message, path=self.path)


class WorkerFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    message: NonEmptyText
    path: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2048),
    ] | None


class WorkerMetadataResponse(BaseModel):
    """The nested metadata object returned by the worker HTTP API."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["MISSING", "VALID", "WARNING", "INVALID"]
    source_file_name: SourceFilename | None
    sha256: Sha256 | None
    raw_content: str | None = Field(
        default=None,
        max_length=4 * 1024 * 1024,
        repr=False,
    )
    hex_color: HexColor | None
    width_cm: str | None
    height_cm: str | None
    warnings: list[WorkerFindingResponse]
    errors: list[WorkerFindingResponse]

    @model_validator(mode="after")
    def validate_source_filename(self) -> Self:
        if self.source_file_name is not None and (
            self.source_file_name != self.source_file_name.strip()
            or "/" in self.source_file_name
            or "\\" in self.source_file_name
        ):
            raise ValueError("source_file_name must be a basename")
        return self


class WorkerMaterialPreflightResponse(BaseModel):
    """Exact wire response for worker POST /internal/material-preflight."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    folder_path: FolderPath
    folder_name: FolderName
    master_resolution: MasterResolution | None
    master_last_modified_at: str | None
    policy: MaterialZipPolicy | None
    metadata: WorkerMetadataResponse
    warnings: list[WorkerFindingResponse]
    errors: list[WorkerFindingResponse]
    can_continue: bool

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.folder_name != self.folder_name.strip():
            raise ValueError("folder_name must not contain surrounding whitespace")
        if "/" in self.folder_name or "\\" in self.folder_name:
            raise ValueError("folder_name must be a single path component")
        if self.can_continue != (not self.errors):
            raise ValueError("can_continue must correspond to the absence of top-level errors")
        return self

    def to_internal(self) -> "WorkerMaterialPreflight":
        """Map the worker wire contract into the backend's operation model."""
        return WorkerMaterialPreflight(
            schema_version=self.schema_version,
            folder_path=self.folder_path,
            folder_name=self.folder_name,
            master_resolution=self.master_resolution,
            master_last_modified_at=self.master_last_modified_at,
            policy=self.policy,
            metadata_status=MaterialMetadataStatus(self.metadata.status),
            source_filename=self.metadata.source_file_name,
            sha256=self.metadata.sha256,
            raw_content=self.metadata.raw_content,
            hex_color=self.metadata.hex_color,
            width_cm=(
                Decimal(self.metadata.width_cm)
                if self.metadata.width_cm is not None
                else None
            ),
            height_cm=(
                Decimal(self.metadata.height_cm)
                if self.metadata.height_cm is not None
                else None
            ),
            metadata_warnings=[item.to_internal() for item in self.metadata.warnings],
            metadata_errors=[item.to_internal() for item in self.metadata.errors],
            warnings=[item.to_internal() for item in self.warnings],
            errors=[item.to_internal() for item in self.errors],
            can_continue=self.can_continue,
        )


class WorkerMaterialPreflight(BaseModel):
    """Backend-internal normalized preflight used by Material Done operations."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    folder_path: FolderPath
    folder_name: FolderName
    master_resolution: MasterResolution | None
    master_last_modified_at: str | None
    policy: MaterialZipPolicy | None
    metadata_status: MaterialMetadataStatus
    source_filename: SourceFilename | None
    sha256: Sha256 | None
    raw_content: str | None = Field(
        default=None,
        max_length=4 * 1024 * 1024,
        repr=False,
    )
    hex_color: HexColor | None
    width_cm: Decimal | None = Field(gt=0, max_digits=12, decimal_places=4)
    height_cm: Decimal | None = Field(gt=0, max_digits=12, decimal_places=4)
    metadata_warnings: list[WorkerFinding]
    metadata_errors: list[WorkerFinding]
    warnings: list[WorkerFinding]
    errors: list[WorkerFinding]
    can_continue: bool

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.folder_name != self.folder_name.strip():
            raise ValueError("folder_name must not contain surrounding whitespace")
        if "/" in self.folder_name or "\\" in self.folder_name:
            raise ValueError("folder_name must be a single path component")
        if self.source_filename is not None and (
            self.source_filename != self.source_filename.strip()
            or "/" in self.source_filename
            or "\\" in self.source_filename
        ):
            raise ValueError("source_filename must be a basename")
        if self.can_continue != (not self.errors):
            raise ValueError("can_continue must correspond to the absence of errors")
        return self


class MaterialPreflightClient(Protocol):
    def preflight(self, folder_path: str) -> WorkerMaterialPreflight: ...


class WorkerClient:
    def __init__(self, base_url: str, timeout_seconds: float = 2.0) -> None:
        normalized_base_url = base_url.rstrip("/")
        if not normalized_base_url.startswith(("http://", "https://")):
            raise ValueError("WORKER_BASE_URL must use http or https")
        if not 0 < timeout_seconds <= 10:
            raise ValueError("Worker timeout must be greater than zero and at most 10 seconds")
        self._url = f"{normalized_base_url}/internal/material-preflight"
        self._timeout = httpx.Timeout(timeout_seconds)

    def preflight(self, folder_path: str) -> WorkerMaterialPreflight:
        try:
            with httpx.stream(
                "POST",
                self._url,
                json={"folder_path": folder_path},
                timeout=self._timeout,
            ) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    raise WorkerUnavailableError("Material worker is unavailable.")

                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_size = int(content_length)
                    except ValueError as exc:
                        raise WorkerResponseError(
                            "Material worker returned an invalid response."
                        ) from exc
                    if declared_size < 0 or declared_size > MAX_WORKER_RESPONSE_BYTES:
                        raise WorkerResponseError(
                            "Material worker returned an invalid response."
                        )

                chunks: list[bytes] = []
                decoded_size = 0
                for chunk in response.iter_bytes(chunk_size=WORKER_RESPONSE_CHUNK_BYTES):
                    decoded_size += len(chunk)
                    if decoded_size > MAX_WORKER_RESPONSE_BYTES:
                        raise WorkerResponseError(
                            "Material worker returned an invalid response."
                        )
                    chunks.append(chunk)
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise WorkerUnavailableError("Material worker is unavailable.") from exc

        content = b"".join(chunks)
        try:
            wire_response = WorkerMaterialPreflightResponse.model_validate_json(
                content,
                strict=True,
            )
            if wire_response.folder_path != folder_path:
                raise ValueError("Worker folder_path does not match the request")
            return wire_response.to_internal()
        except (ArithmeticError, ValidationError, ValueError):
            # Do not chain validation details: Pydantic errors can contain raw_content.
            raise WorkerResponseError("Material worker returned an invalid response.") from None
