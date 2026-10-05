"""Read-only Windows counterpart of worker material preflight, not Auto-check.

Only immediate resolution directories and bounded source metadata are inspected.
Native no-follow handles anchor the material and metadata; map bytes are not read.
"""
from contextlib import ExitStack
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
import re
import stat
from zoneinfo import ZoneInfo

from app.db.models import MaterialMetadataStatus
from app.inventory_client import validate_relative_path
from app.local_filesystem import LocalFilesError
from app.metadata_client import MetadataValues
from app.metadata_document import _unique, _invalid
from app.schemas import MaterialZipPolicy
from app.worker_client import WorkerFinding, WorkerMaterialPreflight, WorkerUnavailableError

MAX_METADATA_BYTES = 4 * 1024 * 1024
RESOLUTION = re.compile(r"[1-9][0-9]*K", re.ASCII)
STANDARD_RESOLUTIONS = {"1K", "2K", "4K", "8K", "16K"}
SIZE = re.compile(r"texture size: (-?[0-9]+(?:\.[0-9]+)?)x(-?[0-9]+(?:\.[0-9]+)?) cm", re.ASCII)


def _finding(code, path, message):
    return WorkerFinding(code=code, path=path, message=message)


def _metadata(raw, filename, folder, error=None):
    """Keep metadata problems advisory, just as worker/source_metadata.py does."""
    result = dict(metadata_status=MaterialMetadataStatus.MISSING, source_filename=None, sha256=None,
        raw_content=None, hex_color=None, width_cm=None, height_cm=None, metadata_warnings=[], metadata_errors=[])
    def warn(code, message):
        result["metadata_warnings"].append(_finding(code, folder + "/" + filename, message))
    def invalid(code, message):
        result["metadata_status"] = MaterialMetadataStatus.INVALID
        result["source_filename"] = filename
        result["metadata_errors"].append(_finding(code, folder + "/" + filename, message))
        return result
    if error:
        return invalid(error, "Source metadata could not be safely read; its values are unavailable.")
    if raw is None:
        warn("SOURCE_METADATA_MISSING", "Source metadata does not exist.")
        return result
    result["source_filename"] = filename
    result["sha256"] = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return invalid("SOURCE_METADATA_INVALID_FORMAT", "Source metadata is not UTF-8 text.")
    result["raw_content"] = text
    if not text.strip():
        return invalid("SOURCE_METADATA_EMPTY", "Source metadata is empty.")
    def dimension(field, value):
        try:
            if not isinstance(value, Decimal) or not value.is_finite() or not 0 < value < Decimal("100000000"):
                raise ValueError()
            digits = value.as_tuple().digits
            trailing = 0
            for digit in reversed(digits):
                if digit != 0: break
                trailing += 1
            if value.as_tuple().exponent + trailing < -4: raise ValueError()
            # The database has Numeric(12,4); preserve exact values including
            # valid long trailing-zero spellings without formatting exponents.
            result[field] = value
        except (ValueError, ArithmeticError):
            warn("SOURCE_METADATA_INVALID_DIMENSION", "Sample dimensions must be positive and fit eight integer and four fractional digits.")
    if text.lstrip().startswith(("{", "[")):
        try:
            data = json.loads(text, parse_float=Decimal, parse_int=Decimal, object_pairs_hook=_unique, parse_constant=_invalid)
            if not isinstance(data, dict) or any(key in data for key in ("WEB_APP_PART", "DESKTOP_APP_PART")):
                raise ValueError()
        except (ValueError, ArithmeticError, RecursionError):
            return invalid("SOURCE_METADATA_INVALID_FORMAT", "Expected a strict source metadata object.")
        color = data.get("COLOR", {})
        if isinstance(color, dict) and "hex" not in color:
            warn("HEX_COLOR_MISSING", "COLOR.hex is missing.")
        else:
            try:
                value = color.get("hex") if isinstance(color, dict) else None
                if not isinstance(value, str): raise ValueError()
                result["hex_color"] = MetadataValues(hex_color=value, width_cm=None, height_cm=None).hex_color
            except ValueError:
                warn("HEX_COLOR_INVALID", "COLOR.hex must contain six hexadecimal digits.")
        size = data.get("TEXTURE_SIZE", {})
        cm = size.get("cm", {}) if isinstance(size, dict) else {}
        for name in ("width", "height"):
            dimension(name + "_cm", cm.get(name) if isinstance(cm, dict) else None)
    else:
        warn("HEX_COLOR_MISSING", "The observed legacy text format has no documented hex color.")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        size_lines = [line for line in lines if line.startswith("texture size:")]
        match = SIZE.fullmatch(size_lines[0]) if len(size_lines) == 1 else None
        if match is None:
            return invalid("SOURCE_METADATA_INVALID_FORMAT", "Expected exactly one texture size: <width>x<height> cm line.")
        for name, token in zip(("width_cm", "height_cm"), match.groups()):
            dimension(name, Decimal(token))
        if len(lines) != 1:
            warn("SOURCE_METADATA_UNRECOGNIZED_CONTENT", "Additional legacy source lines were ignored.")
    result["metadata_status"] = MaterialMetadataStatus.WARNING if result["metadata_warnings"] else MaterialMetadataStatus.VALID
    return result


class LocalMaterialPreflight:
    def __init__(self, library):
        self.library = library

    def preflight(self, folder_path):
        try:
            validate_relative_path(folder_path)
        except ValueError:
            raise WorkerUnavailableError("Material folder path is invalid or unsafe.") from None
        values = dict(schema_version=1, folder_path=folder_path, folder_name=folder_path.rsplit("/", 1)[-1],
            master_resolution=None, master_last_modified_at=None, policy=None, warnings=[], errors=[])
        metadata = _metadata(None, "metadata.json", folder_path)
        fs = self.library.fs
        try:
            with self.library.lock, fs.directory(folder_path) as directory, ExitStack() as pins:
                resolutions = []
                with os.scandir(directory) as entries:
                    for index, entry in enumerate(entries):
                        if index >= 4096: raise LocalFilesError("LOCAL_ENTRY_LIMIT")
                        if not RESOLUTION.fullmatch(entry.name): continue
                        info = entry.stat(follow_symlinks=False)
                        if stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400:
                            continue
                        try:
                            pins.enter_context(fs.opened(directory / entry.name, directory=True))
                            info = os.stat(directory / entry.name, follow_symlinks=False)
                            resolutions.append((entry.name, info.st_mtime_ns))
                            if entry.name not in STANDARD_RESOLUTIONS:
                                values["warnings"].append(_finding("NON_STANDARD_RESOLUTION", folder_path + "/" + entry.name,
                                    entry.name + " is usable but is not a standard resolution."))
                        except (OSError, ValueError, LocalFilesError):
                            values["errors"].append(_finding("UNSAFE_RESOLUTION", folder_path + "/" + entry.name,
                                "Resolution is linked, inaccessible, or not a plain directory."))
                if not resolutions:
                    values["errors"].append(_finding("NO_RESOLUTION", folder_path, "No resolution directory found."))
                elif not values["errors"]:
                    name, modified_ns = max(resolutions, key=lambda item: (len(item[0]), item[0]))
                    seconds, nanos = divmod(modified_ns, 1_000_000_000)
                    instant = datetime.fromtimestamp(seconds, timezone.utc)
                    boundary = datetime(2026, 3, 4, tzinfo=ZoneInfo(os.environ.get("ZIP_POLICY_TIMEZONE", "Europe/Prague")))
                    values.update(master_resolution=name, master_last_modified_at=instant.strftime("%Y-%m-%dT%H:%M:%S") + f".{nanos:09d}+00:00",
                        policy=MaterialZipPolicy.LEGACY_BEFORE_2026_03_04 if instant < boundary else MaterialZipPolicy.CURRENT_ON_OR_AFTER_2026_03_04)
                filename = "metadata.json"
                try:
                    (directory / filename).lstat()
                except FileNotFoundError:
                    filename = "metadata.txt"
                except OSError:
                    pass  # Try the safe opener; inaccessible JSON must not fall back to TXT.
                try:
                    raw = fs.read(folder_path + "/" + filename, limit=MAX_METADATA_BYTES)
                    metadata = _metadata(raw, filename, folder_path)
                except FileNotFoundError:
                    metadata = _metadata(None, filename, folder_path)
                except (OSError, ValueError, LocalFilesError) as exc:
                    code = "SOURCE_METADATA_TOO_LARGE" if getattr(exc, "code", "") == "LOCAL_FILE_TOO_LARGE" else "SOURCE_METADATA_UNREADABLE"
                    metadata = _metadata(None, filename, folder_path, code)
        except (OSError, ValueError, LocalFilesError):
            values["errors"].append(_finding("MATERIAL_INSPECTION_FAILED", folder_path, "Material folder could not be safely inspected."))
        try:
            return WorkerMaterialPreflight(**values, **metadata, can_continue=not values["errors"])
        except ValueError:
            raise WorkerUnavailableError("Local material preflight could not produce a valid bounded result.") from None
