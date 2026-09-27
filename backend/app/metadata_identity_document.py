"""Strict identity-only production JSON rewrite; editable measurements stay exact."""
import json
import re
from app.material_naming import base_name, match_identity
from app.metadata_document import MAX_BYTES as MAX_METADATA_BYTES, _unique as _unique_object, _invalid as _reject_constant


class MetadataIdentityError(ValueError):
    """Fixed identity protocol error, never source content."""


class JsonNumber(str):
    """Retain source JSON number tokens exactly, including precision/exponents."""


def _encode(value, depth=0):
    if depth > 64: raise MetadataIdentityError("METADATA_REWRITE_UNSUPPORTED")
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


def rewrite_identity_metadata(raw: bytes, old_identity: str, *, new_identity: str, brand_name: str, material_name: str) -> tuple[bytes, list[str]]:
    """Pure transform; bytes stay internal and are never part of a plan response."""
    old_match = match_identity(old_identity); new_match = match_identity(new_identity)
    if new_match is None: raise MetadataIdentityError("IDENTITY_TARGET_INVALID")
    if old_match is None: raise MetadataIdentityError("IDENTITY_SOURCE_UNSUPPORTED")
    try:
        if len(raw) > MAX_METADATA_BYTES: raise ValueError()
        text = raw.decode("utf-8")
        if not text.lstrip().startswith("{"): raise ValueError()
        data = json.loads(text, parse_float=JsonNumber, parse_int=JsonNumber,
                          parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        if not isinstance(data, dict) or any(key in data for key in ("WEB_APP_PART", "DESKTOP_APP_PART")): raise ValueError()
        changes = []
        def change(container, key, value, label):
            if container[key] != value or type(container[key]) is not type(value):
                container[key] = value; changes.append(label)
        for field, value in (("FOLDER", new_identity), ("MANUFACTURER", brand_name),
                             ("PRODUCT_NAME", material_name), ("CATEGORY", new_match["category"])):
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
                    raise MetadataIdentityError("METADATA_UNMAPPED_REFERENCE")
        ensure_no_old_reference(data)
        rewritten = (_encode(data) + "\n").encode("utf-8") if changes else raw
        if len(rewritten) > MAX_METADATA_BYTES: raise ValueError()
        return rewritten, sorted(changes)
    except MetadataIdentityError: raise
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise MetadataIdentityError("METADATA_REWRITE_UNSUPPORTED") from None
