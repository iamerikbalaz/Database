"""Full, read-only source checks shared by desktop checks and offline export.

The legacy technical report remains an internal packaging input. The separate
PBR_FILES_V1 result certifies the stronger source rules, never an old approval.
"""
from copy import deepcopy
import os
from pathlib import Path
import re
import stat
import time

from app.inventory import AUTOMATIC_FILES_SCOPE, InventoryError, InventoryLimits, _safe_name, inventory_material
from app.material_naming import NAMED_IDENTITY, map_bases
from app.secure_filesystem import _metadata_flags, open_material_directory
from app.technical_validation import STRICT_PROBE_WALL_SECONDS, VALIDATION_SLOT, _validate_material, probe_image

PROFILE = "PBR_FILES_V1"
RGB_JPEG = frozenset({"COL", "DIFF", "NRM", "SPEC", "SPECLVL", "SSS", "SSSABSORB", "TRANSL", "ANISO"})
GRAY_JPEG = frozenset({"ROUGH", "GLOSS", "DISP", "SHEENGLOSS", "OPAC", "AO", "METAL"})
MAP_RULES = {**{name: ("JPEG", 8, frozenset({"RGB"})) for name in RGB_JPEG},
    **{name: ("JPEG", 8, frozenset({"L"})) for name in GRAY_JPEG},
    "NRM16": ("PNG", 16, frozenset({"RGB", "RGBA"})),
    "DISP16": ("TIFF", 16, frozenset({"I;16", "I;16B", "I;16L", "L"})),
    "ID": ("PNG", 8, frozenset({"L"})),
    "SHEEN": ("PNG", 8, frozenset({"L", "RGB"}))}
MAX_PREVIEWS = 256
INCOMPLETE_PROBES = frozenset({"IMAGE_PROBE_UNAVAILABLE", "IMAGE_PROBE_FAILED", "IMAGE_PROBE_TIMEOUT", "IMAGE_SOURCE_CHANGED"})


def check_material_files(root: Path, parts: tuple[str, ...], *, for_export: bool = False) -> dict:
    if not parts or not all(_safe_name(part) for part in parts):
        raise ValueError("FILE_CHECK_PATH_INVALID")
    if not VALIDATION_SLOT.acquire(blocking=False):
        raise InventoryError("VALIDATION_BUSY")
    try:
        return _check(root, parts, for_export=for_export)
    finally:
        VALIDATION_SLOT.release()


def _check(root, parts, *, for_export):
    deadline = time.monotonic() + 240
    scope = None if for_export else AUTOMATIC_FILES_SCOPE
    # One initial inventory and one final verification after every image and
    # metadata check. The intermediate legacy verification would reread all
    # inputs before previews, then immediately read them again below.
    technical = _validate_material(root, parts, include_mode=True, inventory_scope=scope, verify_after=False,
        probe_wall_limit=STRICT_PROBE_WALL_SECONDS)
    inventory = technical["inventory"]
    if any(item["code"] in INCOMPLETE_PROBES for item in technical["errors"]):
        raise InventoryError("FILE_CHECK_INCOMPLETE")
    master = inventory["master_resolution"]
    identity = parts[-1]
    issues = []; findings = []; seen = set()

    def add(code, path, expected, actual):
        key = (code, path, expected)
        if key in seen: return
        seen.add(key)
        findings.append({"code": code, "path": path})
        issues.append(f"{path or '.'}: expected {expected}; actual {actual} [{code}]")

    match = NAMED_IDENTITY.fullmatch(identity)
    if (match is None or identity != identity.upper() or int(match["number"]) == 0
            or not re.fullmatch(r"[A-Z0-9]+(?:[-.][A-Z0-9]+)*", match["name"])):
        add("MATERIAL_FOLDER_NAME_INVALID", ".", "UPPERCASE BRAND_0001_FULL-HYPHENATED-NAME_CATEGORY", identity)
    resolutions = [item["path"] for item in inventory["entries"] if item["kind"] == "directory"
        and "/" not in item["path"] and re.fullmatch(r"[0-9]+[Kk]", item["path"])]
    if len(resolutions) != 1 or resolutions[0] != master:
        add("SINGLE_MASTER_REQUIRED", ".", "exactly one canonical highest-resolution folder (for example 4K)",
            (", ".join(resolutions[:10]) + (f" (+{len(resolutions) - 10} more)" if len(resolutions) > 10 else "")) if resolutions else "none")

    descriptions = {
        "MASTER_RESOLUTION_MISSING": ("a source resolution folder", "none"),
        "NO_RESOLUTION": ("a source resolution folder", "none"),
        "MASTER_ENTRY_LIMIT": ("at most 64 master entries", "master entry limit exceeded"),
        "MASTER_NESTED_DIRECTORY": ("map files directly inside the master folder", "nested directory or file"),
        "MAP_FILENAME_INVALID": (f"{' or '.join(map_bases(identity))}_TYPE_{master}.extension", "filename does not match"),
        "MAP_SHORTCUT_UNSUPPORTED": ("a supported map shortcut", "unsupported shortcut"),
        "MAP_SHORTCUT_DUPLICATE": ("one file per map shortcut", "duplicate map"),
        "COLOR_MAP_REQUIRED": ("a readable COL map", "missing or unreadable"),
    }
    # More informative geometry/format messages below replace legacy summaries.
    explained = {"MAP_EXTENSION_MISMATCH", "MAP_16BIT_REQUIRED", "MAP_DIMENSIONS_MISMATCH", "MASTER_BELOW_1K"}
    for item in technical["errors"]:
        if item["code"] in explained: continue
        expected, actual = descriptions.get(item["code"], ("a safely readable valid file", item["code"]))
        add(item["code"], item["path"], expected, actual)
    for item in technical["warnings"]:
        if item["code"].startswith(("SOURCE_METADATA_", "HEX_COLOR_")):
            add(item["code"], item["path"], "valid source metadata with COLOR.hex and positive TEXTURE_SIZE.cm.width/height", item["code"])
    if not any(entry["path"] == "metadata.json" and entry["kind"] == "file" for entry in inventory["entries"]):
        add("SOURCE_METADATA_JSON_REQUIRED", "metadata.json", "a regular root metadata.json file", "missing (legacy metadata.txt is not the canonical source)")

    images = technical["images"]
    present = {image["map"] for image in images}
    for name in ("COL", "ROUGH", "NRM"):
        if name not in present:
            add("REQUIRED_MAP_MISSING", master or ".", f"a readable {name} map", "missing (NRM16 cannot replace NRM; GLOSS cannot replace ROUGH)")
    color = next((image for image in images if image["map"] == "COL"), None)
    for image in images:
        path = image["path"]; shortcut = image["map"]
        rule = MAP_RULES.get(shortcut)
        if rule is None:
            add("MAP_SHORTCUT_UNSUPPORTED", path, "ID for a mask map" if shortcut == "MASK" else "a supported map shortcut", shortcut)
            continue
        fmt, bits, modes = rule
        actual = f"{image['format']}, {image['bits']} bits/channel, {image['mode']}"
        expected_mode = "grayscale" if shortcut == "DISP16" else "/".join(sorted(modes))
        if image["format"] != fmt or image["bits"] != bits or image["mode"] not in modes:
            add("MAP_PIXEL_FORMAT_INVALID", path, f"{fmt}, {bits} bits/channel, {expected_mode}", actual)
        extension = path.rsplit(".", 1)[-1].lower()
        permitted_extensions = {"JPEG": {"jpg", "jpeg"}, "PNG": {"png"}, "TIFF": {"tif", "tiff"}}[fmt]
        if extension not in permitted_extensions or image["format"] != fmt:
            add("MAP_EXTENSION_MISMATCH", path, f"{fmt} file with {'/'.join(sorted(permitted_extensions))} extension", f".{extension}, decoded {image['format']}")
        if color is not None and (image["width"], image["height"]) != (color["width"], color["height"]):
            add("MAP_DIMENSIONS_MISMATCH", path, f"{color['width']}x{color['height']} pixels, matching COL", f"{image['width']}x{image['height']} pixels")
        if master is not None and max(image["width"], image["height"]) != int(master[:-1]) * 1024:
            add("MASTER_DIMENSIONS_DIFFER", path, f"longest side exactly {int(master[:-1]) * 1024} pixels for {master}",
                f"{image['width']}x{image['height']} pixels")

    entries = inventory["entries"]
    previews = [entry for entry in entries if entry["kind"] == "file" and entry["path"].startswith("PREVIEW/")]
    preview_names = {entry["path"] for entry in previews}
    if not {"PREVIEW/SPHERE_1.png", "PREVIEW/FABRIC_1.png"} & preview_names:
        add("PRIMARY_PREVIEW_REQUIRED", "PREVIEW", "SPHERE_1.png or FABRIC_1.png (exact name)", "neither exists")
    if len(previews) > MAX_PREVIEWS:
        add("PREVIEW_ENTRY_LIMIT", "PREVIEW", f"at most {MAX_PREVIEWS} preview files", f"{len(previews)} files")
    else:
        for entry in previews:
            path = entry["path"]
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise InventoryError("INVENTORY_TIME_LIMIT")
            components = path.split("/")
            try:
                with open_material_directory(root, (*parts, *components[:-1])) as directory_fd:
                    fd = os.open(components[-1], _metadata_flags(), dir_fd=directory_fd)
                    try:
                        info = os.fstat(fd)
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise InventoryError("INVENTORY_SOURCE_CHANGED")
                        image = probe_image(fd, timeout=remaining)
                    finally: os.close(fd)
            except OSError:
                raise InventoryError("INVENTORY_SOURCE_CHANGED") from None
            if "error" in image:
                if image["error"] in INCOMPLETE_PROBES:
                    raise InventoryError("FILE_CHECK_INCOMPLETE")
                add(image["error"], path, "a readable PNG preview, 1200x1200 pixels", image["error"])
                continue
            if image["sha256"] != entry["sha256"]: raise InventoryError("INVENTORY_SOURCE_CHANGED")
            if not path.endswith(".png") or image["format"] != "PNG":
                add("PREVIEW_FORMAT_INVALID", path, "PNG file with .png extension", f".{path.rsplit('.', 1)[-1]}, decoded {image['format']}")
            if (image["width"], image["height"]) != (1200, 1200):
                add("PREVIEW_DIMENSIONS_INVALID", path, "1200x1200 pixels", f"{image['width']}x{image['height']} pixels")
    remaining = deadline - time.monotonic()
    if remaining <= 0: raise InventoryError("INVENTORY_TIME_LIMIT")
    latest = inventory_material(root, parts, limits=InventoryLimits(max_seconds=remaining),
        **({"scope": scope} if scope else {}))
    if latest["source_revision_hash"] != inventory["source_revision_hash"]:
        raise InventoryError("INVENTORY_SOURCE_CHANGED")

    # The staging contract consumes only its historical fields. It receives a
    # report only after full checks, and never optional decoder evidence.
    packaging_report = deepcopy(technical) if for_export else None
    if packaging_report is not None:
        for image in packaging_report["images"]: image.pop("mode", None)
    status = "ISSUES" if issues else "OK"
    lines = [f"Material: {identity}", f"Folder: {'/'.join(parts)}", f"Profile: {PROFILE}",
        f"Status: {status}", f"Master: {master or 'missing'}", f"Maps decoded: {len(images)}; previews found: {len(previews)}", ""]
    if not for_export:
        lines.insert(-1, "Checked inputs: resolution folders, PREVIEW and root metadata; authoring/SOURCE payloads excluded.")
    lines.extend(issues or ["All automatic source checks passed."])
    return {"profile": PROFILE, "complete": True, "issues": issues, "report": "\n".join(lines),
        "findings": findings, "packaging_report": packaging_report}
