"""Read only the observed production metadata.txt format, never a web manifest."""

import hashlib
import re
import stat
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.preflight import (
    MAX_METADATA_BYTES, Finding, ZipPolicy, _is_link, preflight_material,
)


# Decimal dot is an explicit extension of the observed integer dimensions.
NUMBER = r"-?[0-9]+(?:\.[0-9]+)?"
SIZE_LINE = re.compile(rf"texture size: ({NUMBER})x({NUMBER}) cm", re.ASCII)


@dataclass
class SourceMetadataResult:
    source_filename: str = "metadata.txt"
    status: str = "NOT_CHECKED"
    sha256: str | None = None
    hex_color: str | None = None
    width_cm: Decimal | None = None
    height_cm: Decimal | None = None
    master_resolution: str | None = None
    master_modified_at: str | None = None
    selected_zip_policy: ZipPolicy | None = None
    warnings: list[Finding] = field(default_factory=list)
    errors: list[Finding] = field(default_factory=list)

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
    """Validate a supplied color; its location in production input is unconfirmed.

    Not used to guess a field or scan arbitrary source text for color tokens.
    """
    if not isinstance(value, str) or not re.fullmatch(r"#?[0-9a-fA-F]{6}", value):
        raise ValueError("Hex color must contain exactly six hexadecimal digits")
    return "#" + value.removeprefix("#").upper()


def parse_source_metadata(material_path: str | Path, *, allowed_root: str | Path,
                          boundary: datetime | None = None) -> SourceMetadataResult:
    """Inspect a stable trusted local snapshot; metadata issues never add errors.

    Shares path guards, master selection and timezone policy with preflight.
    Does not read metadata.json, web-manifest.json, maps or nested metadata.
    OS-managed atime requires a noatime/read-only snapshot (no timestamp writes).
    """
    preflight = preflight_material(material_path, allowed_root=allowed_root,
                                   boundary=boundary, inspect_web_manifest=False)
    result = SourceMetadataResult(
        master_resolution=preflight.master_resolution,
        master_modified_at=preflight.master_modified_at,
        selected_zip_policy=preflight.selected_zip_policy,
        warnings=list(preflight.warnings), errors=list(preflight.errors),
    )
    if result.errors:
        return result
    path = Path(material_path) / result.source_filename

    def warn(code: str, message: str) -> None:
        result.warnings.append(Finding(code, str(path), message))

    def stop(status: str, message: str) -> SourceMetadataResult:
        result.status = status
        warn("SOURCE_METADATA_" + status, message)
        return result

    try:
        info = path.lstat()
        if _is_link(info) or not stat.S_ISREG(info.st_mode):
            return stop("UNSAFE_FILE", "Source metadata must be a plain regular file")
        if info.st_size > MAX_METADATA_BYTES:
            return stop("TOO_LARGE", "Source metadata exceeds the 4 MiB limit; no partial hash returned")
        with path.open("rb") as source:
            raw = source.read(MAX_METADATA_BYTES + 1)
        if len(raw) > MAX_METADATA_BYTES:
            return stop("TOO_LARGE", "Source metadata grew beyond the 4 MiB limit")
    except FileNotFoundError:
        return stop("MISSING", "Root metadata.txt does not exist")
    except OSError:
        return stop("UNREADABLE", "Root metadata.txt could not be read")

    result.sha256 = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return stop("INVALID_FORMAT", "Source metadata is not UTF-8 text")
    if not text.strip():
        return stop("EMPTY", "Source metadata is empty")

    # Never assume undocumented COLOR.hex / TEXTURE_SIZE.cm JSON fields.
    warn("SOURCE_METADATA_HEX_MISSING",
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
    result.status = "PARTIAL"  # The observed valid format does not carry a color.
    return result
