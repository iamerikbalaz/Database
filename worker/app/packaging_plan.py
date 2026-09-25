"""Pure, approval-bound packaging layout. This module performs no IO or conversion."""
from dataclasses import asdict, dataclass
import hashlib
import json
import re

from app.material_naming import base_name, map_bases, match_identity
from app.image_probe import MAX_PIXELS, MAX_SIDE
from app.inventory import _safe_name
from app.preflight import MAX_METADATA_BYTES, ZipPolicy
from app.technical_validation import FORMATS, MAPS


class PackagingPlanError(ValueError):
    """Fixed error codes without source content or paths."""


def _require(condition, code="PACKAGING_INPUT_INVALID"):
    if not condition: raise PackagingPlanError(code)


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _positive(value, maximum):
    return type(value) is int and 0 < value <= maximum


def _relative(value):
    return (isinstance(value, str) and 0 < len(value.encode("utf-8", "surrogatepass")) <= 2048
        and all(_safe_name(part) for part in value.split("/")))


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _fit(width, height, side):
    # Positive integer round-half-up. The conversion verifier must check the
    # actual runtime dimensions, not just trust this expected geometry.
    longest = max(width, height)
    return tuple(max(1, (value * side * 2 + longest) // (longest * 2)) for value in (width, height))


@dataclass(frozen=True)
class MapOperation:
    source: str
    destination: str
    source_sha256: str
    shortcut: str
    format: str
    bits: int
    width: int
    height: int
    action: str
    input_is_effective_master: bool


@dataclass(frozen=True)
class PackageResolution:
    name: str
    width: int
    height: int
    archive_name: str
    archive_root: str
    maps: tuple[MapOperation, ...]
    # Relative paths inside archive_root. Byte hashes are filled after staging.
    archive_entries: tuple[str, ...]


@dataclass(frozen=True)
class CopiedInput:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class PackagingPlan:
    schema_version: int
    identity: str
    source_revision_hash: str
    policy: str
    source_master: str
    effective_master: str
    resolutions: tuple[PackageResolution, ...]
    previews: tuple[CopiedInput, ...]
    preview_directories: tuple[str, ...]
    production_metadata: CopiedInput | None
    warnings: tuple[str, ...]

    @property
    def sha256(self): return _digest(asdict(self))

    def web_manifest(self) -> bytes:
        master = self.resolutions[0]
        # Keep the historical manifest keys and h/w meaning. Its six significant
        # digits match the archived awk output; valid JSON has no trailing comma.
        value = {"WEB_APP_PART": {"TEXTURE_RESOLUTIONS": {item.name: f"{item.width}x{item.height}" for item in self.resolutions},
            "IMAGE_RATIO": float(format(master.height / master.width, ".6g")),
            "MAPS_SHORTCUTS": [item.shortcut for item in master.maps]}, "DESKTOP_APP_PART": {}}
        return (json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False) + "\n").encode()


def build_packaging_plan(report: dict, *, expected_source_revision_hash: str, policy: str) -> PackagingPlan:
    """Accept a current validated report and explicitly persisted ZIP policy.

    The caller must prove real authorization, current approvals and inventory
    freshness. A matching hash does not grant a filesystem snapshot or permission.
    """
    try:
        return _build(report, expected_source_revision_hash, policy)
    except PackagingPlanError: raise
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise PackagingPlanError("PACKAGING_INPUT_INVALID") from None


def _build(report, expected, policy):
    _require(isinstance(report, dict) and type(report["schema_version"]) is int and report["schema_version"] == 1 and report["validator_version"] == "pbr-images-1"
        and report["can_approve"] is True and report["errors"] == [])
    _require(_hash(expected))
    _require(policy in {item.value for item in ZipPolicy}, "PACKAGING_POLICY_REQUIRED")
    inventory = report["inventory"]
    _require(inventory["schema_version"] == 1 and inventory["source_revision_hash"] == expected, "PACKAGING_SOURCE_CHANGED")
    bound = {key: inventory[key] for key in ("schema_version", "folder_name", "master_resolution", "policy", "entries")}
    _require(_digest(bound) == expected, "PACKAGING_SOURCE_CHANGED")
    identity, master = inventory["folder_name"], inventory["master_resolution"]
    _require(isinstance(identity, str) and _safe_name(identity) and match_identity(identity) is not None)
    _require(isinstance(master, str) and re.fullmatch(r"[1-9][0-9]{0,2}K", master) is not None)
    _require(1 <= int(master[:-1]) <= 32, "PACKAGING_MASTER_UNSUPPORTED")
    entries = inventory["entries"]
    _require(isinstance(entries, list) and 1 <= len(entries) <= 20000)
    by_path = {}; portable = set(); total = 0
    for entry in entries:
        path = entry["path"]
        _require(_relative(path) and path.casefold() not in portable, "PACKAGING_PATH_COLLISION")
        portable.add(path.casefold()); by_path[path] = entry
        _require(entry["kind"] in {"file", "directory"} and type(entry["size"]) is int and entry["size"] >= 0)
        _require((_hash(entry["sha256"]) and entry["size"] <= 64 * 1024**3) if entry["kind"] == "file" else entry["size"] == 0 and entry["sha256"] is None)
        total += entry["size"]
    _require(total == inventory["total_bytes"] and total <= 256 * 1024**3)
    for path in by_path:
        parent = path.rpartition("/")[0]
        _require(not parent or (parent in by_path and by_path[parent]["kind"] == "directory"))
    _require(master in by_path and by_path[master]["kind"] == "directory")
    images = report["images"]
    _require(isinstance(images, list) and 1 <= len(images) <= len(MAPS))
    parsed = []; seen = set()
    pattern = re.compile("(?:" + "|".join(re.escape(base) for base in map_bases(identity)) + r")_([A-Z0-9]+)_" + re.escape(master) + r"\.([a-zA-Z]+)$")
    for image in images:
        path = image["path"]; match = pattern.fullmatch(path.removeprefix(master + "/"))
        _require(path.startswith(master + "/") and match is not None)
        shortcut, extension = match.groups()
        _require(shortcut in MAPS and shortcut not in seen and image["map"] == shortcut)
        seen.add(shortcut)
        _require(extension.lower() in FORMATS and image["format"] == FORMATS[extension.lower()])
        _require(path in by_path and by_path[path]["kind"] == "file" and image["sha256"] == by_path[path]["sha256"], "PACKAGING_SOURCE_CHANGED")
        _require(_positive(image["width"], MAX_SIDE) and _positive(image["height"], MAX_SIDE) and image["width"] * image["height"] <= MAX_PIXELS)
        _require(type(image["bits"]) is int and image["bits"] in {1, 2, 4, 8, 16} and (not shortcut.endswith("16") or image["bits"] == 16))
        parsed.append((shortcut, extension, image))
    _require("COL" in seen, "PACKAGING_COLOR_REQUIRED")
    parsed.sort(key=lambda item: item[0])
    color = next(image for shortcut, _, image in parsed if shortcut == "COL")
    width, height = color["width"], color["height"]
    _require(all((image["width"], image["height"]) == (width, height) for _, _, image in parsed), "PACKAGING_DIMENSIONS_MISMATCH")
    image_paths = {image["path"] for _, _, image in parsed}
    _require({path for path in by_path if path.startswith(master + "/")} <= image_paths | {master + "/metadata.txt"}, "PACKAGING_UNREVIEWED_MASTER_ENTRY")
    actual_k = max(width, height) // 1024
    _require(actual_k >= 1, "PACKAGING_MASTER_BELOW_1K")
    effective_k = min(int(master[:-1]), actual_k)
    effective = f"{effective_k}K"
    effective_width, effective_height = _fit(width, height, effective_k * 1024)
    metadata = by_path.get("metadata.txt")
    _require(metadata is None or (metadata["kind"] == "file" and metadata["size"] <= MAX_METADATA_BYTES))
    nested_metadata = by_path.get(master + "/metadata.txt")
    _require(nested_metadata is None or (metadata is not None and nested_metadata["kind"] == "file" and nested_metadata["sha256"] == metadata["sha256"]), "PACKAGING_METADATA_COLLISION")
    previews = tuple(CopiedInput(entry["path"], entry["size"], entry["sha256"]) for entry in entries if entry["path"].startswith("PREVIEW/") and entry["kind"] == "file")
    directories = tuple(sorted(entry["path"] + "/" for entry in entries if (entry["path"] == "PREVIEW" or entry["path"].startswith("PREVIEW/")) and entry["kind"] == "directory"))
    resolutions = []
    for number in [effective_k, *(number for number in (16, 8, 4, 2, 1) if number < effective_k)]:
        name = f"{number}K"; archive_root = f"{identity}_{name}"
        _require(_safe_name(archive_root + ".zip"), "PACKAGING_OUTPUT_NAME_LIMIT")
        target_width, target_height = _fit(effective_width, effective_height, number * 1024)
        operations = []
        for shortcut, extension, image in parsed:
            destination = f"{name}/{base_name(identity)}_{shortcut}_{name}.{extension}"
            _require(_relative(destination), "PACKAGING_OUTPUT_NAME_LIMIT")
            # Historical scripts copy only an exact square master; rectangles
            # are re-encoded even when their longest side already matches.
            action = "COPY" if number == effective_k and width == height == number * 1024 else "RESIZE"
            operations.append(MapOperation(image["path"], destination, image["sha256"], shortcut, image["format"], image["bits"], target_width, target_height, action, number != effective_k))
        archive_entries = tuple(sorted(["metadata.json", name + "/", *directories, *(item.path for item in previews),
            *(item.destination for item in operations), *([name + "/metadata.txt"] if metadata is not None else [])]))
        resolutions.append(PackageResolution(name, target_width, target_height, archive_root + ".zip", archive_root, tuple(operations), archive_entries))
    return PackagingPlan(1, identity, expected, policy, master, effective, tuple(resolutions), tuple(sorted(previews, key=lambda item: item.path)), directories,
        CopiedInput("metadata.txt", metadata["size"], metadata["sha256"]) if metadata else None,
        tuple(code for code, needed in (("PRODUCTION_METADATA_MISSING", metadata is None), ("PREVIEW_MISSING", not previews)) if needed))
