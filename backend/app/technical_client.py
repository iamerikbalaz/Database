"""Validate image facts against the bounded source inventory wire contract."""
from typing import Annotated, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.inventory_client import SourceInventory, WorkerInventoryClient, validate_relative_path
from app.worker_client import FolderPath, Sha256
from app.inventory_client import InventoryClientError

MAPS = {"AO", "COL", "DISP16", "DISP", "GLOSS", "NRM16", "NRM", "ROUGH"}
FORMATS = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "tif": "TIFF", "tiff": "TIFF", "webp": "WEBP"}
FINDING_CODES = frozenset({
    "IMAGE_DIMENSION_LIMIT", "IMAGE_MULTIFRAME_UNSUPPORTED", "IMAGE_UNREADABLE", "IMAGE_BIT_DEPTH_UNSUPPORTED",
    "IMAGE_MODE_UNSUPPORTED", "IMAGE_SOURCE_CHANGED", "IMAGE_PROBE_UNAVAILABLE", "IMAGE_RESOURCE_LIMIT",
    "IMAGE_PROBE_FAILED", "IMAGE_PROBE_TIMEOUT", "MASTER_RESOLUTION_MISSING", "MASTER_ENTRY_LIMIT",
    "MASTER_NESTED_DIRECTORY", "MAP_FILENAME_INVALID", "MAP_SHORTCUT_UNSUPPORTED", "MAP_SHORTCUT_DUPLICATE",
    "MAP_EXTENSION_MISMATCH", "MAP_16BIT_REQUIRED", "COLOR_MAP_REQUIRED", "MAP_DIMENSIONS_MISMATCH",
    "MASTER_BELOW_1K", "MASTER_DIMENSIONS_DIFFER", "NORMAL_MAP_MISSING", "SURFACE_RESPONSE_MAP_MISSING",
    "PREVIEW_MISSING", "SOURCE_METADATA_MISSING", "SOURCE_METADATA_TOO_LARGE", "SOURCE_METADATA_INVALID_FORMAT",
    "SOURCE_METADATA_EMPTY", "SOURCE_METADATA_UNSAFE_FILE", "SOURCE_METADATA_UNREADABLE",
    "SOURCE_METADATA_DIMENSION_UNSUPPORTED", "SOURCE_METADATA_INVALID_DIMENSION", "SOURCE_METADATA_DIMENSIONS_MISSING",
    "SOURCE_METADATA_DIMENSIONS_AMBIGUOUS", "SOURCE_METADATA_UNRECOGNIZED_CONTENT", "HEX_COLOR_MISSING", "HEX_COLOR_INVALID",
    "MATERIAL_INSPECTION_FAILED", "UNSAFE_RESOLUTION", "NON_STANDARD_RESOLUTION", "NO_RESOLUTION",
})


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,99}$")]
    path: Annotated[str, Field(max_length=2048)]

    @model_validator(mode="after")
    def check(self) -> Self:
        if self.code not in FINDING_CODES: raise ValueError("Unsupported validation finding")
        if self.path: validate_relative_path(self.path)
        return self


class ImageFact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: FolderPath
    map: Literal["AO", "COL", "DISP16", "DISP", "GLOSS", "NRM16", "NRM", "ROUGH"]
    width: Annotated[int, Field(ge=1, le=32768)]
    height: Annotated[int, Field(ge=1, le=32768)]
    bits: Literal[1, 2, 4, 8, 16]
    format: Literal["PNG", "JPEG", "TIFF", "WEBP"]
    sha256: Sha256


class TechnicalReport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1]
    validator_version: Literal["pbr-images-1"]
    inventory: SourceInventory
    images: Annotated[list[ImageFact], Field(max_length=64)]
    errors: Annotated[list[Finding], Field(max_length=256)]
    warnings: Annotated[list[Finding], Field(max_length=256)]
    can_approve: bool

    @model_validator(mode="after")
    def check(self) -> Self:
        if self.can_approve != (not self.errors):
            raise ValueError("Contradictory validation outcome")
        inventory = self.inventory
        files = {entry.path: entry for entry in inventory.entries if entry.kind == "file"}
        if len({image.path for image in self.images}) != len(self.images) or len({image.map for image in self.images}) != len(self.images):
            raise ValueError("Duplicate image proof")
        for image in self.images:
            if image.path not in files or files[image.path].sha256 != image.sha256 or image.width * image.height > 16384**2:
                raise ValueError("Image proof does not match inventory")
        # Do not accept a success boolean without complete, consistent map facts.
        if self.can_approve:
            master = inventory.master_resolution
            color = next((image for image in self.images if image.map == "COL"), None)
            if master is None or color is None or max(color.width, color.height) < 1024:
                raise ValueError("Color map is required")
            master_paths = {entry.path for entry in inventory.entries if entry.path.startswith(master + "/")}
            expected_paths = {image.path for image in self.images}
            if master_paths - {master + "/metadata.txt"} != expected_paths:
                raise ValueError("Master entries are not fully verified")
            for image in self.images:
                from app.material_naming import map_bases
                prefixes = (f"{master}/{base}_{image.map}_{master}." for base in map_bases(inventory.folder_name))
                filename_matches = any(image.path.startswith(prefix) and FORMATS.get(image.path[len(prefix):].lower()) == image.format
                                       for prefix in prefixes)
                if (not filename_matches
                        or (image.width, image.height) != (color.width, color.height)
                        or (image.map.endswith("16") and image.bits != 16)):
                    raise ValueError("Inconsistent successful image proof")
        return self


class TechnicalClient(Protocol):
    def validate(self, folder_path: str) -> TechnicalReport: ...


class WorkerTechnicalClient(WorkerInventoryClient):
    def __init__(self, base_url: str, timeout_seconds: float = 125):
        super().__init__(base_url, timeout_seconds)
        self.url = base_url.rstrip("/") + "/internal/material-validate"

    def validate(self, folder_path: str) -> TechnicalReport:
        try:
            report = TechnicalReport.model_validate_json(self.read_content(folder_path), strict=True)
            if report.inventory.folder_name != folder_path.rsplit("/", 1)[-1]:
                raise ValueError("Validation identity does not match request")
            return report
        except InventoryClientError:
            raise
        except (ValueError, TypeError, AttributeError, ArithmeticError):
            raise InventoryClientError() from None
