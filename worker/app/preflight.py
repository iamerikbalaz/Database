"""Read-only inspection of one material; no packaging or job integration."""

import hashlib
import json
import os
import re
import stat
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo


class ZipPolicy(StrEnum):
    LEGACY_BEFORE_2026_03_04 = "LEGACY_BEFORE_2026_03_04"
    CURRENT_ON_OR_AFTER_2026_03_04 = "CURRENT_ON_OR_AFTER_2026_03_04"


RESOLUTION_PATTERN = re.compile(r"[1-9][0-9]*K", re.ASCII)
STANDARD_RESOLUTIONS = frozenset({"1K", "2K", "4K", "8K", "16K"})
DEFAULT_POLICY_TIMEZONE = "Europe/Prague"
MAX_METADATA_BYTES = 4 * 1024 * 1024
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def configured_policy_boundary() -> datetime:
    """Storage timezone is configuration, never the machine's local timezone."""
    zone = ZoneInfo(os.environ.get("ZIP_POLICY_TIMEZONE", DEFAULT_POLICY_TIMEZONE))
    return datetime(2026, 3, 4, tzinfo=zone)


def _boundary_ns(boundary: datetime) -> int:
    if boundary.utcoffset() is None:
        raise ValueError("Policy boundary must include an explicit timezone")
    if (boundary.year, boundary.month, boundary.day,
            boundary.hour, boundary.minute, boundary.second, boundary.microsecond) != (
            2026, 3, 4, 0, 0, 0, 0):
        raise ValueError("Policy boundary must be local midnight on 2026-03-04")
    delta = boundary.astimezone(timezone.utc) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000_000


def select_zip_policy(master_modified_ns: int, *, boundary: datetime | None = None) -> ZipPolicy:
    """Compare exact filesystem nanoseconds, including the inclusive boundary."""
    boundary = boundary if boundary is not None else configured_policy_boundary()
    _boundary_ns(boundary)
    seconds, _ = divmod(master_modified_ns, 1_000_000_000)
    modified = datetime.fromtimestamp(seconds, timezone.utc).astimezone(boundary.tzinfo)
    # Boundary is a whole second. Floor division preserves even the final
    # nanosecond before it without rounding a float timestamp onto midnight.
    if modified < boundary:
        return ZipPolicy.LEGACY_BEFORE_2026_03_04
    return ZipPolicy.CURRENT_ON_OR_AFTER_2026_03_04


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    message: str


@dataclass
class PreflightResult:
    material_path: str
    policy_boundary: str
    policy_timezone: str
    found_resolutions: list[str] = field(default_factory=list)
    master_resolution: str | None = None
    master_modified_at: str | None = None
    master_modified_ns: int | None = None
    selected_zip_policy: ZipPolicy | None = None
    metadata_status: str = "NOT_CHECKED"
    metadata_exists: bool | None = None
    metadata_readable: bool | None = None
    metadata_strict_json: bool | None = None
    metadata_sha256: str | None = None
    metadata_fields_present: dict[str, bool] = field(default_factory=dict)
    metadata_web_app_part: dict | None = None
    warnings: list[Finding] = field(default_factory=list)
    errors: list[Finding] = field(default_factory=list)

    @property
    def can_continue(self) -> bool:
        """Policy inspection readiness; never a permission check for Done."""
        return not self.errors

    def to_dict(self) -> dict:
        return {**asdict(self), "can_continue": self.can_continue}


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _local_path(path: Path) -> None:
    # Check spelling before any filesystem access (also rejects Windows UNC on POSIX).
    if str(path).startswith(("\\\\", "//")):
        raise ValueError("Network and device paths are not allowed")
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Use an absolute local path without '..'")


def _check_directory_chain(path: Path) -> None:
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        if _is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"Not a plain directory: {component}")


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON constant: {value}")


def _unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(text: str):
    return json.loads(text, parse_constant=_reject_constant,
                      object_pairs_hook=_unique_object)


def _inspect_metadata(path: Path, result: PreflightResult) -> None:
    warnings_before = len(result.warnings)
    def warn(code: str, message: str) -> None:
        result.warnings.append(Finding(code, str(path), message))

    try:
        info = path.lstat()
        result.metadata_exists = True
        if _is_link(info) or not stat.S_ISREG(info.st_mode):
            result.metadata_status = "UNSAFE_FILE"
            result.metadata_readable = False
            warn("METADATA_UNSAFE_FILE", "Metadata must be a plain regular file")
            return
        if info.st_size > MAX_METADATA_BYTES:
            result.metadata_status = "TOO_LARGE"
            warn("METADATA_TOO_LARGE", "Metadata exceeds the 4 MiB inspection limit")
            return
        with path.open("rb") as source:
            raw = source.read(MAX_METADATA_BYTES + 1)
        result.metadata_readable = True
        if len(raw) > MAX_METADATA_BYTES:
            result.metadata_status = "TOO_LARGE"
            warn("METADATA_TOO_LARGE", "Metadata grew beyond the inspection limit")
            return
    except FileNotFoundError:
        result.metadata_exists = False
        result.metadata_readable = False
        result.metadata_status = "MISSING"
        warn("METADATA_MISSING", "metadata.json does not exist")
        return
    except OSError as exc:
        result.metadata_status = "UNREADABLE"
        result.metadata_readable = False
        warn("METADATA_UNREADABLE", str(exc))
        return

    result.metadata_sha256 = hashlib.sha256(raw).hexdigest()
    result.metadata_strict_json = False
    try:
        text = raw.decode("utf-8")
        try:
            data = _load_json(text)
            result.metadata_strict_json = True
        except json.JSONDecodeError:
            # Only the observed final root comma; never repair nested commas,
            # comments, strings, or the source file. Require the manifest shape.
            match = re.search(r",(?=\s*}\s*\Z)", text)
            if match is None:
                raise
            data = _load_json(text[:match.start()] + text[match.end():])
            if not isinstance(data, dict) or not {
                "WEB_APP_PART", "DESKTOP_APP_PART"
            }.issubset(data):
                raise ValueError("Not a recognized legacy manifest")
            warn("LEGACY_NON_STANDARD_JSON",
                 "Input is not strict JSON: final root comma tolerated in memory only")
    except (ValueError, RecursionError) as exc:
        result.metadata_status = "INVALID_JSON"
        warn("METADATA_INVALID_JSON", str(exc))
        return

    result.metadata_status = "VALID" if result.metadata_strict_json else "LEGACY_NON_STANDARD_JSON"
    web = data.get("WEB_APP_PART") if isinstance(data, dict) else None
    result.metadata_web_app_part = web if isinstance(web, dict) else None
    result.metadata_fields_present = {
        "WEB_APP_PART": isinstance(data, dict) and "WEB_APP_PART" in data,
        "WEB_APP_PART.TEXTURE_RESOLUTIONS": isinstance(web, dict) and "TEXTURE_RESOLUTIONS" in web,
        "WEB_APP_PART.MAPS_SHORTCUTS": isinstance(web, dict) and "MAPS_SHORTCUTS" in web,
    }
    expected = {"WEB_APP_PART": (web, dict)}
    if isinstance(web, dict):
        expected.update({
            "WEB_APP_PART.TEXTURE_RESOLUTIONS": (web.get("TEXTURE_RESOLUTIONS"), dict),
            "WEB_APP_PART.MAPS_SHORTCUTS": (web.get("MAPS_SHORTCUTS"), list),
        })
    for name, present in result.metadata_fields_present.items():
        if not present:
            warn("METADATA_MISSING_FIELD", name)
    for name, (value, kind) in expected.items():
        if result.metadata_fields_present[name] and not isinstance(value, kind):
            warn("METADATA_INVALID_FIELD_TYPE", name)
    if result.metadata_strict_json and len(result.warnings) > warnings_before:
        result.metadata_status = "INVALID_STRUCTURE"


def preflight_material(material_path: str | Path, *, allowed_root: str | Path,
                       boundary: datetime | None = None) -> PreflightResult:
    """Inspect direct resolution directories and root metadata in a trusted local tree.

    Default midnight uses ZIP_POLICY_TIMEZONE (Europe/Prague unless configured).
    Caller may also supply an explicit boundary. No writes, shell calls,
    recursive map reads, ZIPs, or automatic worker polling are performed.
    Use a read-only/noatime snapshot for OS-level timestamp and race guarantees.
    """
    boundary = boundary if boundary is not None else configured_policy_boundary()
    _boundary_ns(boundary)  # Configuration errors fail before any source access.
    path, root = Path(material_path), Path(allowed_root)
    result = PreflightResult(str(path), boundary.isoformat(),
                             getattr(boundary.tzinfo, "key", str(boundary.tzinfo)))
    try:
        _local_path(root)
        _local_path(path)
        if not path.is_relative_to(root):
            raise ValueError("Material is outside the allowed root")
        _check_directory_chain(path)
        with os.scandir(path) as entries:
            for entry in entries:
                if not RESOLUTION_PATTERN.fullmatch(entry.name):
                    continue
                info = entry.stat(follow_symlinks=False)
                if _is_link(info):
                    result.errors.append(Finding(
                        "UNSAFE_RESOLUTION", entry.path, "Resolution links are not followed"))
                elif stat.S_ISDIR(info.st_mode):
                    result.found_resolutions.append(entry.name)
                    if entry.name not in STANDARD_RESOLUTIONS:
                        result.warnings.append(Finding(
                            "NON_STANDARD_RESOLUTION", entry.path,
                            f"{entry.name} is usable but is not a standard resolution"))
        # Length then lexicographic order is numeric for canonical positive integers.
        result.found_resolutions.sort(key=lambda name: (len(name), name))
        if not result.found_resolutions:
            result.errors.append(Finding("NO_RESOLUTION", str(path), "No resolution directory found"))
        elif not result.errors:
            result.master_resolution = result.found_resolutions[-1]
            master = path / result.master_resolution
            info = master.lstat()
            if _is_link(info) or not stat.S_ISDIR(info.st_mode):
                raise ValueError("Master changed or is not a plain directory")
            result.master_modified_ns = info.st_mtime_ns
            seconds, nanos = divmod(info.st_mtime_ns, 1_000_000_000)
            instant = datetime.fromtimestamp(seconds, timezone.utc)
            result.master_modified_at = instant.strftime("%Y-%m-%dT%H:%M:%S") + f".{nanos:09d}+00:00"
            result.selected_zip_policy = select_zip_policy(info.st_mtime_ns, boundary=boundary)
    except (OSError, ValueError, OverflowError) as exc:
        result.errors.append(Finding("MATERIAL_INSPECTION_FAILED", str(path), str(exc)))
        return result
    _inspect_metadata(path / "metadata.json", result)
    return result
