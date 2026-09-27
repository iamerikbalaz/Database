"""Strict, bounded source-metadata protocol; response bodies stay private."""
from decimal import Decimal, localcontext
import hashlib
import json
import re
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.inventory_client import WorkerInventoryClient, validate_relative_path
from app.worker_client import FolderName, HexColor, Sha256


class MetadataClientError(RuntimeError):
    def __init__(self): super().__init__("Source metadata could not be verified.")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MetadataValues(StrictModel):
    hex_color: str | None
    width_cm: Annotated[str, Field(max_length=32)] | None
    height_cm: Annotated[str, Field(max_length=32)] | None

    @field_validator("hex_color")
    @classmethod
    def color(cls, value):
        if value is None: return None
        if re.fullmatch(r"#?[0-9A-Fa-f]{6}", value) is None: raise ValueError("Use six hex digits")
        return "#" + value.removeprefix("#").upper()

    @field_validator("width_cm", "height_cm")
    @classmethod
    def dimension(cls, value):
        if value is None: return None
        if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value) is None: raise ValueError("Use a positive decimal")
        number = Decimal(value)
        with localcontext() as context:
            context.prec = 64
            if not 0 < number < Decimal("100000000") or number != number.quantize(Decimal("0.0001")):
                raise ValueError("Use up to eight integer and four fractional digits")
        return format(number, "f").rstrip("0").rstrip(".") if "." in value else str(number)


class SourceMetadata(StrictModel):
    schema_version: Annotated[int, Field(ge=1, le=1)]
    folder_name: FolderName
    status: Literal["MISSING", "VALID", "WARNING", "INVALID"]
    sha256: Sha256 | None
    hex_color: HexColor | None
    width_cm: str | None
    height_cm: str | None
    raw_content: str | None = Field(max_length=4 * 1024 * 1024, repr=False)

    @model_validator(mode="after")
    def verify(self):
        MetadataValues(hex_color=self.hex_color, width_cm=self.width_cm, height_cm=self.height_cm)
        if (self.raw_content is None) != (self.sha256 is None): raise ValueError("Missing source proof")
        if self.raw_content is not None:
            raw = self.raw_content.encode("utf-8")
            if len(raw) > 4 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != self.sha256:
                raise ValueError("Source proof mismatch")
        if (self.status == "MISSING") != (self.sha256 is None): raise ValueError("Missing source status mismatch")
        return self


class MetadataObservation(SourceMetadata):
    editable: bool
    writes_enabled: bool


class MetadataResult(StrictModel):
    operation_id: str
    status: Literal["COMPLETED", "REJECTED"]
    failure_code: Literal["METADATA_SOURCE_CHANGED", "METADATA_FORMAT_UNSUPPORTED", "SOURCE_METADATA_UNSAFE_FILE",
                          "SOURCE_METADATA_UNREADABLE", "SOURCE_METADATA_TOO_LARGE"] | None
    metadata: SourceMetadata | None

    @model_validator(mode="after")
    def verify(self):
        if (self.status == "COMPLETED") != (self.metadata is not None and self.failure_code is None):
            raise ValueError("Inconsistent source result")
        if self.status == "REJECTED" and (self.metadata is not None or self.failure_code is None):
            raise ValueError("Inconsistent rejection")
        return self


class WorkerMetadataClient(WorkerInventoryClient):
    def __init__(self, base_url, token=None, enabled=False, timeout_seconds=30):
        super().__init__(base_url, timeout_seconds)
        self.base_url = base_url.rstrip("/"); self.token = token; self.enabled = enabled

    def _request(self, path, payload, model, *, mutation=False):
        if mutation and (not self.enabled or self.token is None): raise MetadataClientError()
        headers = {"Authorization": "Bearer " + self.token.get_secret_value()} if mutation else {}
        try:
            with httpx.stream("POST", self.base_url + path, json=payload, headers=headers,
                              timeout=self.timeout, trust_env=False, follow_redirects=False) as response:
                if response.status_code != 200: raise MetadataClientError()
                limit = 26 * 1024 * 1024  # JSON escaping of a bounded 4 MiB source.
                declared = response.headers.get("Content-Length")
                if declared is not None and not 0 <= int(declared) <= limit: raise ValueError()
                chunks = []; size = 0
                for chunk in response.iter_bytes(chunk_size=65536):
                    size += len(chunk)
                    if size > limit: raise ValueError()
                    chunks.append(chunk)
                return model.model_validate_json(b"".join(chunks))
        except (httpx.HTTPError, ValueError, TypeError, ArithmeticError): raise MetadataClientError() from None

    def inspect(self, folder_path):
        validate_relative_path(folder_path)
        result = self._request("/internal/material-metadata", {"folder_path": folder_path}, MetadataObservation)
        if result.folder_name != folder_path.rsplit("/", 1)[-1]: raise MetadataClientError()
        return result

    def execute(self, request):
        result = self._request("/internal/material-metadata-edit", request, MetadataResult, mutation=True)
        if result.operation_id != request["operation_id"]: raise MetadataClientError()
        if result.metadata:
            values = MetadataValues(**{name: getattr(result.metadata, name) for name in MetadataValues.model_fields})
            if (result.metadata.folder_name != request["folder_path"].rsplit("/", 1)[-1]
                    or values.model_dump() != request["values"] or result.metadata.status not in {"VALID", "WARNING"}):
                raise MetadataClientError()
            try:
                def unique(pairs):
                    value = {}
                    for key, item in pairs:
                        if key in value: raise ValueError()
                        value[key] = item
                    return value
                def invalid_constant(_): raise ValueError()
                document = json.loads(result.metadata.raw_content, parse_float=Decimal, parse_int=Decimal,
                                      parse_constant=invalid_constant, object_pairs_hook=unique)
                color = document.get("COLOR", {})
                dimensions = document.get("TEXTURE_SIZE", {}).get("cm", {})
                if any(name in dimensions and not isinstance(dimensions[name], Decimal) for name in ("width", "height")):
                    raise ValueError()
                source_values = {"hex_color": color.get("hex"), **{
                    name + "_cm": str(dimensions[name]) if isinstance(dimensions.get(name), Decimal) else None
                    for name in ("width", "height")}}
                if MetadataValues(**source_values).model_dump() != request["values"]: raise ValueError()
            except (ValueError, TypeError, AttributeError, RecursionError): raise MetadataClientError() from None
        return result
