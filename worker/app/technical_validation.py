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

MAPS = frozenset({"AO", "COL", "DISP16", "DISP", "GLOSS", "NRM16", "NRM", "ROUGH"})
FORMATS = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "tif": "TIFF", "tiff": "TIFF", "webp": "WEBP"}
PROBE_ERRORS = frozenset({"IMAGE_DIMENSION_LIMIT", "IMAGE_MULTIFRAME_UNSUPPORTED", "IMAGE_UNREADABLE",
    "IMAGE_BIT_DEPTH_UNSUPPORTED", "IMAGE_MODE_UNSUPPORTED", "IMAGE_SOURCE_CHANGED", "IMAGE_PROBE_UNAVAILABLE",
    "IMAGE_RESOURCE_LIMIT", "IMAGE_PROBE_FAILED", "IMAGE_PROBE_TIMEOUT"})
VALIDATION_SLOT = BoundedSemaphore(1)


def probe_image(fd: int, *, timeout: float = 35) -> dict:
    from app.packaging_lease import inherited_lease_fds
    try:
        result = subprocess.run([sys.executable, "-m", "app.image_probe", str(fd)], pass_fds=inherited_lease_fds((fd,)),
            cwd=Path(__file__).resolve().parent.parent, env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=max(.1, min(timeout, 35)), check=False)
        if len(result.stdout) > 4096: return {"error": "IMAGE_PROBE_FAILED"}
        value = json.loads(result.stdout)
        if not isinstance(value, dict): return {"error": "IMAGE_PROBE_FAILED"}
        if "error" in value:
            return {"error": value["error"] if value["error"] in PROBE_ERRORS else "IMAGE_PROBE_FAILED"}
        if result.returncode != 0: return {"error": "IMAGE_PROBE_FAILED"}
        if (set(value) != {"width", "height", "bits", "format", "sha256"}
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


def _validate_material(root: Path, parts: tuple[str, ...]) -> dict:
    deadline = time.monotonic() + 120
    inventory = inventory_material(root, parts)
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
                    # metadata.txt is copied unchanged alongside maps in derived packages.
                    if name == "metadata.txt": continue
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
                            image = probe_image(fd, timeout=remaining)
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
                if max(color["width"], color["height"]) < 1024:
                    errors.append(finding("MASTER_BELOW_1K", color["path"]))
                if max(color["width"], color["height"]) != int(master[:-1]) * 1024:
                    warnings.append(finding("MASTER_DIMENSIONS_DIFFER", color["path"]))
            if not any(image["map"] in {"NRM", "NRM16"} for image in images): warnings.append(finding("NORMAL_MAP_MISSING", master))
            if not any(image["map"] in {"ROUGH", "GLOSS"} for image in images): warnings.append(finding("SURFACE_RESPONSE_MAP_MISSING", master))
    if not any(entry["kind"] == "file" and entry["path"].startswith("PREVIEW/") for entry in inventory["entries"]):
        warnings.append(finding("PREVIEW_MISSING"))
    metadata = inspect_material_secure(root, parts)
    metadata_entry = next((entry for entry in inventory["entries"] if entry["path"] == "metadata.txt"), None)
    if metadata.sha256 is not None and (metadata_entry is None or metadata.sha256 != metadata_entry["sha256"]):
        raise InventoryError("INVENTORY_SOURCE_CHANGED")
    warnings.extend(finding(item.code, "metadata.txt") for item in metadata.warnings)
    warnings.extend(finding(item.code, master or "") for item in metadata.master_warnings)
    errors.extend(finding(item.code, master or "") for item in [*metadata.errors, *metadata.master_errors])
    if time.monotonic() > deadline: raise InventoryError("INVENTORY_TIME_LIMIT")
    latest = inventory_material(root, parts, limits=InventoryLimits(max_seconds=deadline - time.monotonic()))
    if latest["source_revision_hash"] != inventory["source_revision_hash"]:
        raise InventoryError("INVENTORY_SOURCE_CHANGED")
    return {"schema_version": 1, "validator_version": "pbr-images-1", "inventory": latest,
            "images": images, "errors": errors, "warnings": warnings, "can_approve": not errors}
