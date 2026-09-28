"""Pure production metadata.json transform, also shipped with the source worker.

No filesystem or database access. Existing measurements and unknown fields stay
intact; derived values are updated only when their editable input changes.
"""
from decimal import Decimal, localcontext
import json
import re

MAX_BYTES = 4 * 1024 * 1024
IDENTITY_FIELDS = frozenset({"FOLDER", "MANUFACTURER", "PRODUCT_NUMBER", "PRODUCT_NAME", "CATEGORY", "BASE_NAME"})
RESOLUTION_NAMES = frozenset({"1K", "2K", "4K", "8K", "16K"})


def metadata_template():
    """The supplied production schema, with unknown facts explicitly empty.

    This is a new tree on every call. No measurement, map or source filename
    from the example material is reused for another material.
    """
    return {
        "FOLDER": None, "MANUFACTURER": None, "PRODUCT_NUMBER": None,
        "PRODUCT_NAME": None, "CATEGORY": None, "BASE_NAME": None,
        "TEXTURE_SIZE": {"cm": {"width": None, "height": None},
                         "in": {"width": None, "height": None}, "method": None},
        "TEXTURE_SIZE_SOURCE": None,
        "COLOR": {"hex": None, "delta_e": None,
                  "runner_up": {"hex": None, "delta_e": None},
                  "average_hex": None, "average_rgb": [], "method": None,
                  "measured_from": None,
                  "alternatives": {"mean": None, "median": None, "dominant": None, "chromatic": None}},
        "MAPS_SHORTCUTS": [], "RESOLUTIONS": {},
        "SOURCE": {"CROPS": [], "SBS": None, "REFERENCES": None, "ARCHIVE": None},
        "WARNINGS": [],
    }


def _complete_shape(data, template):
    for key, default in template.items():
        if key not in data or data[key] is None and isinstance(default, (dict, list)):
            data[key] = default
        elif isinstance(default, dict):
            if not isinstance(data[key], dict): raise ValueError("METADATA_FORMAT_UNSUPPORTED")
            _complete_shape(data[key], default)
        elif isinstance(default, list) and not isinstance(data[key], list):
            raise ValueError("METADATA_FORMAT_UNSUPPORTED")


def metadata_inventory_facts(inventory, identity):
    """Derive filename facts from a *complete*, previously secured inventory.

    Entries have material-relative POSIX ``path`` and ``kind`` (file/directory).
    This function performs no IO and makes no claim about image measurements.
    Extra inventory proof fields are allowed. Callers retain responsibility for
    their filesystem proof and for freezing the result in the operation journal.
    """
    if not isinstance(inventory, (list, tuple)) or len(inventory) > 20_000:
        raise ValueError("METADATA_INVENTORY_INVALID")
    paths = {}
    for item in inventory:
        if not isinstance(item, dict): raise ValueError("METADATA_INVENTORY_INVALID")
        path, kind = item.get("path"), item.get("kind")
        if (not isinstance(path, str) or not path or len(path.encode("utf-8", "surrogatepass")) > 2048
                or kind not in {"file", "directory"} or path in paths
                or any(c in path for c in "\\:")
                or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in path)
                or any(not part or part in {".", ".."} or len(part.encode("utf-8")) > 255 for part in path.split("/"))
                or len(path.split("/")) > 16):
            raise ValueError("METADATA_INVENTORY_INVALID")
        paths[path] = kind
    # Reject inconsistent inventories rather than deriving absence from them.
    for path in paths:
        parts = path.split("/")
        for depth in range(1, len(parts)):
            if paths.get("/".join(parts[:depth])) != "directory":
                raise ValueError("METADATA_INVENTORY_INVALID")
    bases = {identity.get("BASE_NAME"), identity.get("FOLDER")} if identity else set()
    bases = {value for value in bases if isinstance(value, str) and value}
    resolutions = {}
    for resolution in sorted(RESOLUTION_NAMES, key=lambda name: int(name[:-1])):
        if paths.get(resolution) != "directory": continue
        maps = set(); unrecognized = []; count = 0
        prefix = resolution + "/"
        for path, kind in sorted(paths.items()):
            if kind != "file" or not path.startswith(prefix): continue
            count += 1; name = path[len(prefix):]
            match = re.fullmatch(r"(.+)_([A-Z][A-Z0-9]{0,15})_" + resolution + r"\.([A-Za-z]+)", name)
            if ("/" not in name and match and match[1] in bases
                    and match[3].lower() in {"png", "jpg", "jpeg", "tif", "tiff", "webp"}):
                maps.add(match[2])
            else: unrecognized.append(name)
        resolutions[resolution] = {"MAPS_SHORTCUTS": sorted(maps), "FILE_COUNT": count, "UNRECOGNIZED": unrecognized}
    files = [path for path, kind in paths.items() if kind == "file"]
    # Prefer the conventional root SBS, otherwise accept only an unambiguous
    # source file. Its presence is not evidence that it produced the cm values.
    sbs = sorted(path for path in files if path.lower().endswith(".sbs")
                 and ("/" not in path or path.split("/", 1)[0].upper() == "SOURCE"))
    expected_sbs = (identity.get("FOLDER") + ".sbs") if identity and isinstance(identity.get("FOLDER"), str) else None
    selected_sbs = expected_sbs if expected_sbs in sbs else sbs[0] if len(sbs) == 1 else None
    crops = set()
    for path in files:
        parts = path.split("/")
        if len(parts) < 2 or parts[0].upper() != "SOURCE": continue
        stem = parts[-1].rsplit(".", 1)[0].lower()
        if len(parts) >= 3 and parts[-2].upper() == "CROPS" and stem in {"col", "disp", "nrm"}:
            crops.add(stem)
        match = re.search(r"(?:^|[_-])(?:crop[_-](col|disp|nrm)|(col|disp|nrm)[_-]crop)$", stem)
        if match: crops.add(match[1] or match[2])
    directories = {path.upper() for path, kind in paths.items() if kind == "directory"}
    return {"MAPS_SHORTCUTS": sorted({name for item in resolutions.values() for name in item["MAPS_SHORTCUTS"]}),
            "RESOLUTIONS": resolutions,
            "SOURCE": {"CROPS": sorted(crops), "SBS": selected_sbs,
                       "REFERENCES": "SOURCE/REFERENCES" in directories,
                       "ARCHIVE": "SOURCE/ARCHIVE" in directories}}


def _enrich_empty(data, facts):
    """Fill missing facts without replacing source-provided measurement proof."""
    for key, value in facts.items():
        if key not in data or data[key] is None or data[key] == [] or data[key] == {}:
            data[key] = value
        elif isinstance(value, dict) and isinstance(data[key], dict):
            _enrich_empty(data[key], value)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError("METADATA_FORMAT_UNSUPPORTED")
        result[key] = value
    return result


def _invalid(_): raise ValueError("METADATA_FORMAT_UNSUPPORTED")


def _serialize(value, depth=0):
    if depth > 64: raise ValueError("METADATA_FORMAT_UNSUPPORTED")
    if isinstance(value, Decimal): return str(value)
    if isinstance(value, dict):
        if not value: return "{}"
        return "{\n" + ",\n".join("    " * (depth + 1) + json.dumps(k, ensure_ascii=True) + ": " + _serialize(v, depth + 1) for k, v in value.items()) + "\n" + "    " * depth + "}"
    if isinstance(value, list):
        if not value: return "[]"
        return "[\n" + ",\n".join("    " * (depth + 1) + _serialize(v, depth + 1) for v in value) + "\n" + "    " * depth + "]"
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def _template_order(data, template):
    ordered = {}
    for key in (*template, *(key for key in data if key not in template)):
        value = data[key]
        if isinstance(value, dict) and isinstance(template.get(key), dict):
            value = _template_order(value, template[key])
        if key == "RESOLUTIONS" and key in template:
            value = {name: _template_order(entry, {"MAPS_SHORTCUTS": [], "FILE_COUNT": None, "UNRECOGNIZED": []}) for name, entry in value.items()}
        ordered[key] = value
    return ordered


def validate_identity(identity):
    if identity is None: return None
    if not isinstance(identity, dict) or set(identity) != IDENTITY_FIELDS: raise ValueError("METADATA_VALUES_INVALID")
    if any(not isinstance(value, str) or not value or len(value) > 512 or any(ord(c) < 32 for c in value)
           for value in identity.values()): raise ValueError("METADATA_VALUES_INVALID")
    if not re.fullmatch(r"[0-9]{4}", identity["PRODUCT_NUMBER"]): raise ValueError("METADATA_VALUES_INVALID")
    if identity["FOLDER"].rsplit("_", 1) != [identity["BASE_NAME"], identity["CATEGORY"]]:
        raise ValueError("METADATA_VALUES_INVALID")
    return dict(identity)


def rewrite_metadata_json(raw, values, identity=None, *, inventory=None):
    """Return exact JSON numbers; callers validate the three editable values."""
    identity = validate_identity(identity)
    if set(values) != {"hex_color", "width_cm", "height_cm"}: raise ValueError("METADATA_VALUES_INVALID")
    if raw is None: data = {}
    else:
        if len(raw) > MAX_BYTES: raise ValueError("SOURCE_METADATA_TOO_LARGE")
        try:
            text = raw.decode("utf-8")
            if text.lstrip().startswith("{"):
                data = json.loads(text, parse_float=Decimal, parse_int=Decimal,
                                  parse_constant=_invalid, object_pairs_hook=_unique)
            else:
                # Legacy source text is retained verbatim, never discarded.
                lines = [line.strip() for line in text.splitlines() if line.strip().startswith("texture size:")]
                if len(lines) != 1 or not re.fullmatch(r"texture size: [0-9]+(?:\.[0-9]+)?x[0-9]+(?:\.[0-9]+)? cm", lines[0]):
                    raise ValueError()
                data = {"LEGACY_SOURCE_TEXT": text}
            if not isinstance(data, dict) or any(k in data for k in ("WEB_APP_PART", "DESKTOP_APP_PART")): raise ValueError()
            for key in ("COLOR", "TEXTURE_SIZE"):
                if key in data and not isinstance(data[key], dict): raise ValueError()
            for key in ("cm", "in"):
                if key in data.get("TEXTURE_SIZE", {}) and not isinstance(data["TEXTURE_SIZE"][key], dict): raise ValueError()
        except (ValueError, UnicodeError, RecursionError): raise ValueError("METADATA_FORMAT_UNSUPPORTED") from None
    if identity is not None: data.update(identity)
    if inventory is not None: _enrich_empty(data, metadata_inventory_facts(inventory, identity or data))
    _complete_shape(data, metadata_template())
    for resolution in data["RESOLUTIONS"].values():
        if not isinstance(resolution, dict): raise ValueError("METADATA_FORMAT_UNSUPPORTED")
        _complete_shape(resolution, {"MAPS_SHORTCUTS": [], "FILE_COUNT": None, "UNRECOGNIZED": []})
    color = data.setdefault("COLOR", {})
    size = data.setdefault("TEXTURE_SIZE", {})
    cm = size.setdefault("cm", {})
    new_color = values["hex_color"]
    previous_color = color.get("hex")
    if isinstance(previous_color, str) and re.fullmatch(r"#?[0-9A-Fa-f]{6}", previous_color):
        previous_color = "#" + previous_color.removeprefix("#").upper()
    if previous_color != new_color:
        # These distances describe the old selected palette color, unlike the
        # original average/sample measurements which remain useful evidence.
        color["delta_e"] = None
        color["runner_up"]["hex"] = None
        color["runner_up"]["delta_e"] = None
        color["method"] = "manual" if raw is not None else "database"
    color["hex"] = new_color
    dimensions_changed = False
    for key in ("width", "height"):
        token = values[key + "_cm"]
        new_value = Decimal(token) if token is not None else None
        if cm.get(key) != new_value:
            dimensions_changed = True
        inches = size["in"]
        if cm.get(key) != new_value or inches.get(key) is None:
            if new_value is None: inches[key] = None
            else:
                with localcontext() as context:
                    context.prec = 40
                    inches[key] = (new_value / Decimal("2.54")).quantize(Decimal("0.0001"))
        if not isinstance(cm.get(key), Decimal) or cm[key] != new_value:
            cm[key] = new_value
    if dimensions_changed: size["method"] = "manual" if raw is not None else "database"
    result = (_serialize(_template_order(data, metadata_template())) + "\n").encode("utf-8")
    if len(result) > MAX_BYTES: raise ValueError("SOURCE_METADATA_TOO_LARGE")
    return result
