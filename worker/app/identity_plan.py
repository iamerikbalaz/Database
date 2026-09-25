"""Read-only rename planning. Execution must independently revalidate this plan."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re

from app.inventory import InventoryError, _safe_name, inventory_material
from app.material_naming import base_name, match_identity
from app.preflight import MAX_METADATA_BYTES, _reject_constant, _unique_object
from app.secure_filesystem import _metadata_bytes, open_material_directory

class IdentityPlanError(ValueError):
    """A fixed code, without filenames, document contents or parser diagnostics."""


@dataclass(frozen=True)
class IdentityTarget:
    path: str
    brand_name: str
    material_name: str

    def parts(self):
        parts = tuple(self.path.split("/"))
        if len(self.path) > 2048 or not parts or any(not _safe_name(part) for part in parts):
            raise IdentityPlanError("IDENTITY_TARGET_INVALID")
        match = match_identity(parts[-1])
        if match is None or int(match["number"]) == 0:
            raise IdentityPlanError("IDENTITY_TARGET_INVALID")
        for text in (self.brand_name, self.material_name):
            if (not isinstance(text, str) or not text.strip() or len(text) > 255
                    or any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in text)):
                raise IdentityPlanError("IDENTITY_TARGET_INVALID")
        return parts


class JsonNumber(str):
    """Retain source JSON number tokens exactly, including precision/exponents."""


def _encode(value, depth=0):
    if depth > 64: raise IdentityPlanError("METADATA_REWRITE_UNSUPPORTED")
    if isinstance(value, JsonNumber): return str(value)
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(key, ensure_ascii=True) + ":" + _encode(item, depth + 1) for key, item in value.items()) + "}"
    if isinstance(value, list): return "[" + ",".join(_encode(item, depth + 1) for item in value) + "]"
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def renamed_component(name, old_identity, new_identity):
    for old, new in ((old_identity, new_identity), (base_name(old_identity), base_name(new_identity))):
        if name == old or name.startswith((old + "_", old + ".")):
            return new + name[len(old):]
    return name


def rewrite_metadata(raw: bytes, old_identity: str, target: IdentityTarget) -> tuple[bytes, list[str]]:
    """Pure transform; bytes stay internal and are never part of a plan response."""
    target_parts = target.parts(); new_identity = target_parts[-1]
    old_match = match_identity(old_identity); new_match = match_identity(new_identity)
    if old_match is None: raise IdentityPlanError("IDENTITY_SOURCE_UNSUPPORTED")
    try:
        if len(raw) > MAX_METADATA_BYTES: raise ValueError()
        text = raw.decode("utf-8")
        if not text.lstrip().startswith("{"):
            # The observed dimensions-only text format carries no identity.
            from app.source_metadata import SIZE_LINE
            if len(text.splitlines()) != 1 or SIZE_LINE.fullmatch(text.strip()) is None: raise ValueError()
            return raw, []
        data = json.loads(text, parse_float=JsonNumber, parse_int=JsonNumber,
                          parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        if not isinstance(data, dict) or any(key in data for key in ("WEB_APP_PART", "DESKTOP_APP_PART")): raise ValueError()
        changes = []
        def change(container, key, value, label):
            if container[key] != value or type(container[key]) is not type(value):
                container[key] = value; changes.append(label)
        for field, value in (("FOLDER", new_identity), ("MANUFACTURER", target.brand_name),
                             ("PRODUCT_NAME", target.material_name), ("CATEGORY", new_match["category"])):
            if field not in data: continue
            if type(data[field]) is not str: raise ValueError()
            if field == "FOLDER" and data[field] != old_identity: raise ValueError()
            if field == "CATEGORY" and data[field] != old_match["category"]: raise ValueError()
            change(data, field, value, field)
        if "PRODUCT_NUMBER" in data:
            number = data["PRODUCT_NUMBER"]
            if not isinstance(number, str) or not number.isascii() or not number.isdigit() or int(number) != int(old_match["number"]): raise ValueError()
            replacement = JsonNumber(str(int(new_match["number"]))) if isinstance(number, JsonNumber) else new_match["number"]
            change(data, "PRODUCT_NUMBER", replacement, "PRODUCT_NUMBER")
        if "BASE_NAME" in data:
            if data["BASE_NAME"] == old_identity: replacement = new_identity
            elif data["BASE_NAME"] == old_identity.rsplit("_", 1)[0]: replacement = new_identity.rsplit("_", 1)[0]
            else: raise ValueError()
            change(data, "BASE_NAME", replacement, "BASE_NAME")
        references = [(data, "TEXTURE_SIZE_SOURCE", "TEXTURE_SIZE_SOURCE")]
        for section, field in (("COLOR", "measured_from"), ("SOURCE", "SBS")):
            if isinstance(data.get(section), dict): references.append((data[section], field, section + "." + field))
        for container, field, label in references:
            if field not in container or container[field] is None: continue
            if type(container[field]) is not str: raise ValueError()
            value = re.sub(r"[^/\\]+", lambda match: renamed_component(match[0], old_identity, new_identity), container[field])
            change(container, field, value, label)
        def ensure_no_old_reference(value, depth=0):
            if depth > 64: raise ValueError()
            if isinstance(value, dict):
                for key, item in value.items():
                    if any(old != new and old in key for old, new in (
                            (old_identity, new_identity), (base_name(old_identity), base_name(new_identity)))): raise ValueError()
                    ensure_no_old_reference(item, depth + 1)
            elif isinstance(value, list):
                for item in value: ensure_no_old_reference(item, depth + 1)
            elif type(value) is str:
                if any(old != new and old in value for old, new in (
                        (old_identity, new_identity), (base_name(old_identity), base_name(new_identity)))):
                    raise IdentityPlanError("METADATA_UNMAPPED_REFERENCE")
        ensure_no_old_reference(data)
        rewritten = (_encode(data) + "\n").encode("utf-8") if changes else raw
        if len(rewritten) > MAX_METADATA_BYTES: raise ValueError()
        return rewritten, sorted(changes)
    except IdentityPlanError: raise
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise IdentityPlanError("METADATA_REWRITE_UNSUPPORTED") from None


def plan_identity_change(root: Path, source_parts: tuple[str, ...], target: IdentityTarget) -> dict:
    destination = target.parts()
    if (not source_parts or any(not _safe_name(part) for part in source_parts)
            or match_identity(source_parts[-1]) is None):
        raise IdentityPlanError("IDENTITY_SOURCE_UNSUPPORTED")
    if destination[:len(source_parts)] == source_parts:
        raise IdentityPlanError("IDENTITY_TARGET_INSIDE_SOURCE")
    source_path = "/".join(source_parts); target_path = "/".join(destination)
    inventory = inventory_material(root, source_parts)
    errors = []; warnings = []; changes = []; seen = set()
    for entry in inventory["entries"]:
        mapped = "/".join(renamed_component(part, source_parts[-1], destination[-1]) for part in entry["path"].split("/"))
        if len(target_path + "/" + mapped) > 2048 or any(not _safe_name(part) for part in mapped.split("/")):
            errors.append({"code": "IDENTITY_PATH_LIMIT", "path": entry["path"]})
        if mapped.casefold() in seen: errors.append({"code": "IDENTITY_TARGET_COLLISION", "path": mapped})
        seen.add(mapped.casefold())
        if mapped != entry["path"]:
            changes.append({"kind": entry["kind"], "source": entry["path"], "target": mapped, "sha256": entry["sha256"]})
    with open_material_directory(root, source_parts) as source_fd, open_material_directory(root, destination[:-1]) as parent_fd:
        if os.fstat(source_fd).st_dev != os.fstat(parent_fd).st_dev:
            raise IdentityPlanError("IDENTITY_CROSS_DEVICE_UNSUPPORTED")
        with os.scandir(parent_fd) as siblings:
            count = 0
            for entry in siblings:
                count += 1
                if count > 20_000: raise IdentityPlanError("IDENTITY_PARENT_ENTRY_LIMIT")
                if entry.name.casefold() == destination[-1].casefold():
                    is_source = destination[:-1] == source_parts[:-1] and entry.name == source_parts[-1]
                    if not is_source: errors.append({"code": "IDENTITY_TARGET_COLLISION", "path": target_path})
        raw, metadata_error = _metadata_bytes(source_fd)
    metadata = {"before_hash": None, "after_hash": None, "changed_fields": []}
    if metadata_error:
        finding = {"code": "SOURCE_METADATA_" + metadata_error[0], "path": "metadata.txt"}
        (warnings if metadata_error[0] == "MISSING" else errors).append(finding)
    else:
        before_hash = hashlib.sha256(raw).hexdigest()
        original = next((item for item in inventory["entries"] if item["path"] == "metadata.txt"), None)
        if original is None or original["sha256"] != before_hash: raise InventoryError("INVENTORY_SOURCE_CHANGED")
        metadata["before_hash"] = before_hash
        try:
            rewritten, fields = rewrite_metadata(raw, source_parts[-1], target)
            metadata.update(after_hash=hashlib.sha256(rewritten).hexdigest(), changed_fields=fields)
        except IdentityPlanError as exc:
            errors.append({"code": str(exc), "path": "metadata.txt"})
    latest = inventory_material(root, source_parts)
    if latest["source_revision_hash"] != inventory["source_revision_hash"]: raise InventoryError("INVENTORY_SOURCE_CHANGED")
    result = {"schema_version": 1, "planner_version": "identity-plan-1", "source_path": source_path,
        "target_path": target_path, "source_revision_hash": inventory["source_revision_hash"], "changes": changes,
        "metadata": metadata, "errors": errors, "warnings": warnings, "ready": not errors}
    result["plan_hash"] = hashlib.sha256(json.dumps({"plan": result, "brand_name": target.brand_name,
        "material_name": target.material_name}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    return result
