"""Read only the observed production metadata.txt format, never a web manifest."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from app.preflight import (
    MAX_METADATA_BYTES, Finding, ZipPolicy, _local_path, _reject_constant, _unique_object,
)


# Decimal dot is an explicit extension of the observed integer dimensions.
NUMBER = r"-?[0-9]+(?:\.[0-9]+)?"
SIZE_LINE = re.compile(rf"texture size: ({NUMBER})x({NUMBER}) cm", re.ASCII)


@dataclass
class SourceMetadataResult:
    source_filename: str = "metadata.txt"
    status: Literal["NOT_SCANNED", "MISSING", "VALID", "WARNING", "INVALID"] = "NOT_SCANNED"
    sha256: str | None = None
    raw_content: str | None = None
    hex_color: str | None = None
    width_cm: Decimal | None = None
    height_cm: Decimal | None = None
    master_resolution: str | None = None
    master_modified_at: str | None = None
    selected_zip_policy: ZipPolicy | None = None
    warnings: list[Finding] = field(default_factory=list)
    errors: list[Finding] = field(default_factory=list)
    master_warnings: list[Finding] = field(default_factory=list)
    master_errors: list[Finding] = field(default_factory=list)

    @property
    def can_continue(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        """JSON-safe representation: exact decimal strings, never floats."""
        result = asdict(self)
        for name in ("width_cm", "height_cm"):
            value = getattr(self, name)
            result[name] = str(value) if value is not None else None
        return {**result, "can_continue": self.can_continue}


def normalize_hex(value: str) -> str:
    """Normalize only an explicitly supplied COLOR.hex value."""
    if not isinstance(value, str) or not re.fullmatch(r"#?[0-9a-fA-F]{6}", value):
        raise ValueError("Hex color must contain exactly six hexadecimal digits")
    return "#" + value.removeprefix("#").upper()


def parse_source_metadata_bytes(
    raw: bytes | None,
    *,
    metadata_error: tuple[str, str] | None = None,
    master_resolution: str | None = None,
    master_modified_at: str | None = None,
    selected_zip_policy: ZipPolicy | None = None,
    master_warnings: list[Finding] | None = None,
    master_errors: list[Finding] | None = None,
) -> SourceMetadataResult:
    """Parse bytes supplied by a secure opener; never open a filesystem path."""
    result = SourceMetadataResult(
        master_resolution=master_resolution,
        master_modified_at=master_modified_at,
        selected_zip_policy=selected_zip_policy,
        master_warnings=list(master_warnings or []),
        master_errors=list(master_errors or []),
    )
    def warn(code: str, message: str) -> None:
        result.warnings.append(Finding(code, result.source_filename, message))

    def stop(status: str, message: str) -> SourceMetadataResult:
        result.status = "MISSING" if status == "MISSING" else "INVALID"
        warn("SOURCE_METADATA_" + status, message)
        return result

    if metadata_error is not None:
        return stop(*metadata_error)
    if raw is None:
        return stop("MISSING", "Root metadata.txt does not exist")
    if len(raw) > MAX_METADATA_BYTES:
        return stop("TOO_LARGE", "Source metadata exceeds the 4 MiB limit")

    result.sha256 = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return stop("INVALID_FORMAT", "Source metadata is not UTF-8 text")
    result.raw_content = text
    if not text.strip():
        return stop("EMPTY", "Source metadata is empty")

    if text.lstrip().startswith(("{", "[")):
        try:
            data = json.loads(text, parse_float=Decimal, parse_int=Decimal,
                              parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        except (ValueError, RecursionError):
            return stop("INVALID_FORMAT", "Invalid strict source JSON")
        if not isinstance(data, dict) or any(key in data for key in ("WEB_APP_PART", "DESKTOP_APP_PART")):
            return stop("INVALID_FORMAT", "Expected source object, not a web manifest")
        color = data.get("COLOR", {})
        if isinstance(color, dict) and "hex" not in color:
            warn("HEX_COLOR_MISSING", "COLOR.hex is missing")
        else:
            try:
                result.hex_color = normalize_hex(color.get("hex") if isinstance(color, dict) else None)
            except ValueError:
                warn("HEX_COLOR_INVALID", "COLOR.hex must contain six hexadecimal digits")
        size = data.get("TEXTURE_SIZE", {})
        cm = size.get("cm", {}) if isinstance(size, dict) else {}
        for name in ("width", "height"):
            value = cm.get(name) if isinstance(cm, dict) else None
            if isinstance(value, Decimal) and value.is_finite() and value > 0:
                setattr(result, name + "_cm", value)
            else:
                warn("SOURCE_METADATA_INVALID_DIMENSION", "TEXTURE_SIZE.cm." + name + " must be a positive JSON number")
        result.status = "WARNING" if result.warnings else "VALID"
        return result

    # Never assume undocumented COLOR.hex / TEXTURE_SIZE.cm JSON fields.
    warn("HEX_COLOR_MISSING",
         "No documented hex field in the observed source format; color is unavailable")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    size_lines = [line for line in lines if line.startswith("texture size:")]
    if len(size_lines) != 1:
        warn("SOURCE_METADATA_DIMENSIONS_MISSING" if not size_lines else
             "SOURCE_METADATA_DIMENSIONS_AMBIGUOUS", "Expected exactly one texture size line")
        return stop("INVALID_FORMAT", "Input does not match the observed source text format")
    match = SIZE_LINE.fullmatch(size_lines[0])
    if match is None:
        return stop("INVALID_FORMAT", "Expected texture size: <width>x<height> cm")

    for name, token in zip(("width_cm", "height_cm"), match.groups()):
        value = Decimal(token)
        if value <= 0:
            warn("SOURCE_METADATA_INVALID_DIMENSION", name + " must be greater than zero")
        else:
            setattr(result, name, value)
    if len(lines) != 1:
        warn("SOURCE_METADATA_UNRECOGNIZED_CONTENT",
             "Additional source lines are unsupported; no values inferred from them")
    result.status = "WARNING"  # The observed text format does not carry a color.
    return result


def parse_source_metadata(material_path: str | Path, *, allowed_root: str | Path,
                          boundary: datetime | None = None) -> SourceMetadataResult:
    """Compatibility entry point backed only by secure descriptor traversal."""
    result = SourceMetadataResult()
    material, root = Path(material_path), Path(allowed_root)
    try:
        _local_path(root)
        _local_path(material)
        relative = material.relative_to(root)
    except (OSError, ValueError) as exc:
        result.errors.append(Finding("MATERIAL_PATH_INVALID", "", str(exc)))
        return result

    from app.secure_filesystem import (
        MaterialFolderNotFound,
        MaterialsRootUnavailable,
        SecureFilesystemAccessUnavailable,
        UnsafeMaterialPath,
        inspect_material_secure,
    )

    try:
        return inspect_material_secure(root, tuple(relative.parts), boundary=boundary)
    except SecureFilesystemAccessUnavailable as exc:
        result.errors.append(Finding(
            "SECURE_FILESYSTEM_ACCESS_UNAVAILABLE", "", str(exc)
        ))
    except (MaterialFolderNotFound, MaterialsRootUnavailable, UnsafeMaterialPath) as exc:
        result.errors.append(Finding("MATERIAL_PATH_INVALID", "", str(exc)))
    return result
