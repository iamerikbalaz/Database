"""Internal staging contract; it does not define the online importer's layout."""
import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Job = Annotated[str, Field(pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$")]
Generation = Annotated[str, Field(pattern=r"^[1-9][0-9]{0,19}$")]
MAX_OBJECT_BYTES = 16 * 1024**3


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class GcsConfiguration(Strict):
    enabled: bool = False
    bucket_name: str = ""
    staging_prefix: str = ""
    timeout_seconds: Annotated[float, Field(gt=0, le=3600)] = 900.0

    @model_validator(mode="after")
    def check(self):
        if self.enabled:
            # Intentionally support a conservative subset of GCS bucket names.
            if re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", self.bucket_name) is None:
                raise ValueError("GCS bucket configuration invalid")
            if (len(self.staging_prefix) > 128 or re.fullmatch(
                    r"[a-z0-9][a-z0-9_-]*(?:/[a-z0-9][a-z0-9_-]*)*", self.staging_prefix) is None):
                raise ValueError("GCS staging prefix configuration invalid")
        return self


class GcsObjectSpec(Strict):
    job_id: Job
    binding_sha256: Sha
    relative_path: Annotated[str, Field(min_length=1, max_length=900)]
    size: Annotated[int, Field(ge=1, le=MAX_OBJECT_BYTES)]
    sha256: Sha

    @model_validator(mode="after")
    def check(self):
        parts = self.relative_path.split("/")
        if (len(self.relative_path.encode("utf-8")) > 850 or len(parts) > 16
                or any(part in {"", ".", ".."} or len(part.encode("utf-8")) > 255 for part in parts)
                or any(ord(c) < 32 or ord(c) == 127 or c in "\\:" for c in self.relative_path)):
            raise ValueError("GCS relative object path invalid")
        return self

    def object_name(self, configuration: GcsConfiguration) -> str:
        return f"{configuration.staging_prefix}/{self.job_id}/{self.relative_path}"

    def metadata(self) -> dict[str, str]:
        return {"reawote-contract": "staging-v1", "reawote-job": self.job_id,
                "reawote-binding-sha256": self.binding_sha256, "reawote-sha256": self.sha256}


class GcsObjectReceipt(Strict):
    """A point-in-time readback result, never a publication or import decision."""
    spec: GcsObjectSpec
    bucket_name: str
    object_name: str
    generation: Generation
    metageneration: Generation

    @model_validator(mode="after")
    def check(self):
        if any(int(value) > 2**63 - 1 for value in (self.generation, self.metageneration)):
            raise ValueError("GCS generation invalid")
        return self


class GcsError(Exception):
    def __init__(self, code: str):
        self.code = code if code in {
            "GCS_DISABLED", "GCS_CONFIGURATION_INVALID", "GCS_SELECTION_INVALID",
            "GCS_CREDENTIAL_UNAVAILABLE", "GCS_ACCESS_DENIED", "GCS_BUSY",
            "GCS_OBJECT_ABSENT", "GCS_OBJECT_CONFLICT", "GCS_SOURCE_CHANGED",
            "GCS_SOURCE_UNAVAILABLE", "GCS_VERIFICATION_FAILED", "GCS_OUTCOME_UNCERTAIN",
        } else "GCS_OUTCOME_UNCERTAIN"
        super().__init__(self.code)
