from decimal import Decimal
from typing import Annotated, Protocol, Self

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


class WorkerFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    message: NonEmptyText
    path: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2048),
    ] | None


class WorkerMaterialPreflight(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int
    folder_name: FolderName
    master_resolution: MasterResolution | None
    policy: MaterialZipPolicy | None
    metadata_status: MaterialMetadataStatus
    source_filename: SourceFilename
    sha256: Sha256 | None
    raw_content: Annotated[str, StringConstraints(max_length=4 * 1024 * 1024)] | None
    hex_color: HexColor | None
    width_cm: Decimal | None = Field(gt=0, max_digits=12, decimal_places=4)
    height_cm: Decimal | None = Field(gt=0, max_digits=12, decimal_places=4)
    warnings: list[WorkerFinding]
    errors: list[WorkerFinding]
    can_continue: bool

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.schema_version != 1:
            raise ValueError("Unsupported worker schema_version")
        if self.folder_name != self.folder_name.strip():
            raise ValueError("folder_name must not contain surrounding whitespace")
        if "/" in self.folder_name or "\\" in self.folder_name:
            raise ValueError("folder_name must be a single path component")
        if self.source_filename != self.source_filename.strip() or (
            "/" in self.source_filename or "\\" in self.source_filename
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
            return WorkerMaterialPreflight.model_validate_json(content, strict=True)
        except ValidationError as exc:
            raise WorkerResponseError("Material worker returned an invalid response.") from exc
