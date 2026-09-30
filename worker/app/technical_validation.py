"""Read-only PBR map checks, bound to an unchanged full source inventory."""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from threading import BoundedSemaphore

from app.inventory import InventoryError, InventoryLimits, inventory_material
from app.image_probe import MAX_PIXELS, MAX_SIDE
from app.material_naming import map_bases
from app.secure_filesystem import _metadata_flags, inspect_material_secure, open_material_directory

# Historical production materials also carry diffuse, metal, specular, ID and
# mask maps. The archived ZIP scripts process these identically to other maps.
MAPS = frozenset({"AO", "COL", "DIFF", "DISP16", "DISP", "GLOSS", "ID", "MASK", "METAL", "NRM16", "NRM", "ROUGH", "SPEC",
    "SPECLVL", "SSS", "SSSABSORB", "TRANSL", "ANISO", "SHEENGLOSS", "OPAC", "SHEEN"})
FORMATS = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "tif": "TIFF", "tiff": "TIFF", "webp": "WEBP"}
PROBE_ERRORS = frozenset({"IMAGE_DIMENSION_LIMIT", "IMAGE_MULTIFRAME_UNSUPPORTED", "IMAGE_UNREADABLE",
    "IMAGE_BIT_DEPTH_UNSUPPORTED", "IMAGE_MODE_UNSUPPORTED", "IMAGE_SOURCE_CHANGED", "IMAGE_PROBE_UNAVAILABLE",
    "IMAGE_RESOURCE_LIMIT", "IMAGE_PROBE_FAILED", "IMAGE_PROBE_TIMEOUT"})
VALIDATION_SLOT = BoundedSemaphore(1)
DEFAULT_PROBE_WALL_SECONDS = 35
STRICT_PROBE_WALL_SECONDS = 120


def master_dimensions_match(master: str, width: int, height: int) -> bool:
    # The source folder label is the thousands bucket of the longest side;
    # publication output resolutions retain their separate 1024-based policy.
    lower = int(master[:-1]) * 1000
    return lower <= max(width, height) < lower + 1000


def probe_image(fd: int, *, timeout: float = DEFAULT_PROBE_WALL_SECONDS, include_mode: bool = False,
                wall_limit: int = DEFAULT_PROBE_WALL_SECONDS) -> dict:
    from app.packaging_lease import inherited_lease_fds
    # Large 16-bit PNGs on a read-only desktop bind mount can spend most of
    # their wall time waiting for IO. Only the explicit full-check caller opts
    # into the larger bound; child CPU/memory limits remain unchanged.
    if type(wall_limit) is not int or wall_limit not in {DEFAULT_PROBE_WALL_SECONDS, STRICT_PROBE_WALL_SECONDS}:
        return {"error": "IMAGE_PROBE_FAILED"}
    try:
        result = subprocess.run([sys.executable, "-m", "app.image_probe", str(fd), *(["--mode"] if include_mode else [])], pass_fds=inherited_lease_fds((fd,)),
            cwd=Path(__file__).resolve().parent.parent, env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=max(.1, min(timeout, wall_limit)), check=False)
        if len(result.stdout) > 4096: return {"error": "IMAGE_PROBE_FAILED"}
        value = json.loads(result.stdout)
        if not isinstance(value, dict): return {"error": "IMAGE_PROBE_FAILED"}
        if "error" in value:
            return {"error": value["error"] if value["error"] in PROBE_ERRORS else "IMAGE_PROBE_FAILED"}
        if result.returncode != 0: return {"error": "IMAGE_PROBE_FAILED"}
        if (set(value) != ({"width", "height", "bits", "format", "sha256", "mode"} if include_mode else {"width", "height", "bits", "format", "sha256"})
                or (include_mode and value.get("mode") not in {"1", "L", "LA", "P", "RGB", "RGBA", "I;16", "I;16B", "I;16L"})
                or any(type(value.get(field)) is not int or value[field] <= 0 for field in ("width", "height", "bits"))
                or value["format"] not in FORMATS.values()
                or value["width"] > MAX_SIDE or value["height"] > MAX_SIDE or value["width"] * value["height"] > MAX_PIXELS
                or value["bits"] not in {1, 2, 4, 8, 16}
                or not isinstance(value["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"])):
            return {"error": "IMAGE_PROBE_FAILED"}
        return value
    except subprocess.TimeoutExpired:
        return {"error": "IMAGE_PROBE_TIMEOUT"}
    except (OSError, ValueError, TypeError):
        return {"error": "IMAGE_PROBE_FAILED"}


def validate_material(root: Path, parts: tuple[str, ...]) -> dict:
    # One decoder at a time per worker process. Do not build an unbounded queue
    # of expensive image scans from concurrent HTTP requests.
    if not VALIDATION_SLOT.acquire(blocking=False):
        raise InventoryError("VALIDATION_BUSY")
    try:
        return _validate_material(root, parts)
    finally:
        VALIDATION_SLOT.release()


def _validate_material(root: Path, parts: tuple[str, ...], *, include_mode: bool = False,
                       inventory_scope: str | None = None, verify_after: bool = True,
                       probe_wall_limit: int = DEFAULT_PROBE_WALL_SECONDS,
                       prepared_inventory: dict | None = None, image_reader=None) -> dict:
    deadline = time.monotonic() + 120
    inventory = prepared_inventory if prepared_inventory is not None else inventory_material(root, parts, **({"scope": inventory_scope} if inventory_scope else {}))
    errors = []; warnings = []; images = []
    def finding(code: str, path: str = "") -> dict: return {"code": code, "path": path}
    master = inventory["master_resolution"]
    if master is None:
        errors.append(finding("MASTER_RESOLUTION_MISSING"))
    else:
        entries = [entry for entry in inventory["entries"] if entry["path"].startswith(master + "/")]
        if len(entries) > 64:
            errors.append(finding("MASTER_ENTRY_LIMIT", master))
        else:
            pattern = re.compile("(?:" + "|".join(re.escape(base) for base in map_bases(parts[-1])) + r")_([A-Z0-9]+)_" + re.escape(master) + r"\.([a-zA-Z]+)$")
            seen = set()
            with open_material_directory(root, (*parts, master)) as directory_fd:
                for entry in entries:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0: raise InventoryError("INVENTORY_TIME_LIMIT")
                    name = entry["path"][len(master) + 1:]
                    if entry["kind"] != "file" or "/" in name:
                        errors.append(finding("MASTER_NESTED_DIRECTORY", entry["path"])); continue
                    # Production metadata is copied alongside maps, distinct
                    # from the generated manifest at the archive root.
                    if name in {"metadata.txt", "metadata.json"}: continue
                    match = pattern.fullmatch(name)
                    if match is None or match[2].lower() not in FORMATS:
                        errors.append(finding("MAP_FILENAME_INVALID", entry["path"])); continue
                    shortcut, extension = match[1], match[2].lower()
                    if shortcut not in MAPS:
                        errors.append(finding("MAP_SHORTCUT_UNSUPPORTED", entry["path"])); continue
                    if shortcut in seen:
                        errors.append(finding("MAP_SHORTCUT_DUPLICATE", entry["path"])); continue
                    seen.add(shortcut)
                    try:
                        fd = os.open(name, _metadata_flags(), dir_fd=directory_fd)
                        try:
                            info = os.fstat(fd)
                            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                                raise InventoryError("INVENTORY_SOURCE_CHANGED")
                            image = (image_reader(fd, entry, timeout=remaining, include_mode=include_mode,
                                wall_limit=probe_wall_limit) if image_reader is not None else
                                probe_image(fd, timeout=remaining, **({"include_mode": True} if include_mode else {}),
                                **({"wall_limit": probe_wall_limit} if probe_wall_limit != DEFAULT_PROBE_WALL_SECONDS else {}))
                                )
                        finally: os.close(fd)
                    except OSError:
                        raise InventoryError("INVENTORY_SOURCE_CHANGED") from None
                    if "error" in image:
                        errors.append(finding(image["error"], entry["path"])); continue
                    if image["sha256"] != entry["sha256"]:
                        raise InventoryError("INVENTORY_SOURCE_CHANGED")
                    if image["format"] != FORMATS[extension]:
                        errors.append(finding("MAP_EXTENSION_MISMATCH", entry["path"]))
                    if shortcut.endswith("16") and image["bits"] != 16:
                        errors.append(finding("MAP_16BIT_REQUIRED", entry["path"]))
                    images.append({"path": entry["path"], "map": shortcut, **image})
            color = next((image for image in images if image["map"] == "COL"), None)
            if color is None:
                errors.append(finding("COLOR_MAP_REQUIRED", master))
            else:
                for image in images:
                    if (image["width"], image["height"]) != (color["width"], color["height"]):
                        errors.append(finding("MAP_DIMENSIONS_MISMATCH", image["path"]))
                # Legacy technical approval feeds the fixed 1024-based export
                # contract. Full source checks classify 1K from 1000px but do
                # not remove this separate minimum for publication output.
                if max(color["width"], color["height"]) < 1024:
                    errors.append(finding("MASTER_BELOW_1K", color["path"]))
                if not master_dimensions_match(master, color["width"], color["height"]):
                    warnings.append(finding("MASTER_DIMENSIONS_DIFFER", color["path"]))
            if not any(image["map"] in {"NRM", "NRM16"} for image in images): warnings.append(finding("NORMAL_MAP_MISSING", master))
            if not any(image["map"] in {"ROUGH", "GLOSS"} for image in images): warnings.append(finding("SURFACE_RESPONSE_MAP_MISSING", master))
    if not any(entry["kind"] == "file" and entry["path"].startswith("PREVIEW/") for entry in inventory["entries"]):
        warnings.append(finding("PREVIEW_MISSING"))
    metadata = inspect_material_secure(root, parts)
    metadata_entry = next((entry for entry in inventory["entries"] if entry["path"] == metadata.source_filename), None)
    if metadata.sha256 is not None and (metadata_entry is None or metadata.sha256 != metadata_entry["sha256"]):
        raise InventoryError("INVENTORY_SOURCE_CHANGED")
    warnings.extend(finding(item.code, metadata.source_filename) for item in metadata.warnings)
    warnings.extend(finding(item.code, master or "") for item in metadata.master_warnings)
    errors.extend(finding(item.code, master or "") for item in [*metadata.errors, *metadata.master_errors])
    if time.monotonic() > deadline: raise InventoryError("INVENTORY_TIME_LIMIT")
    latest = (inventory_material(root, parts, limits=InventoryLimits(max_seconds=deadline - time.monotonic()),
        **({"scope": inventory_scope} if inventory_scope else {})) if verify_after else inventory)
    if latest["source_revision_hash"] != inventory["source_revision_hash"]:
        raise InventoryError("INVENTORY_SOURCE_CHANGED")
    return {"schema_version": 1, "validator_version": "pbr-images-1", "inventory": latest,
            "images": images, "errors": errors, "warnings": warnings, "can_approve": not errors}
