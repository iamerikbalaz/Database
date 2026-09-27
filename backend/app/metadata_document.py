"""Pure production metadata.json transform, also shipped with the source worker.

No filesystem or database access. Existing measurements and unknown fields stay
intact; derived values are updated only when their editable input changes.
"""
from decimal import Decimal, localcontext
import json
import re

MAX_BYTES = 4 * 1024 * 1024
IDENTITY_FIELDS = frozenset({"FOLDER", "MANUFACTURER", "PRODUCT_NUMBER", "PRODUCT_NAME", "CATEGORY", "BASE_NAME"})


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
        return "{" + ",".join(json.dumps(k, ensure_ascii=True) + ":" + _serialize(v, depth + 1) for k, v in value.items()) + "}"
    if isinstance(value, list): return "[" + ",".join(_serialize(v, depth + 1) for v in value) + "]"
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def validate_identity(identity):
    if identity is None: return None
    if not isinstance(identity, dict) or set(identity) != IDENTITY_FIELDS: raise ValueError("METADATA_VALUES_INVALID")
    if any(not isinstance(value, str) or not value or len(value) > 512 or any(ord(c) < 32 for c in value)
           for value in identity.values()): raise ValueError("METADATA_VALUES_INVALID")
    if not re.fullmatch(r"[0-9]{4}", identity["PRODUCT_NUMBER"]): raise ValueError("METADATA_VALUES_INVALID")
    if identity["FOLDER"].rsplit("_", 1) != [identity["BASE_NAME"], identity["CATEGORY"]]:
        raise ValueError("METADATA_VALUES_INVALID")
    return dict(identity)


def rewrite_metadata_json(raw, values, identity=None):
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
        for key in ("delta_e", "runner_up"): color.pop(key, None)
        color["method"] = "manual" if raw is not None else "database"
    if new_color is None: color.pop("hex", None)
    else: color["hex"] = new_color
    dimensions_changed = False
    for key in ("width", "height"):
        token = values[key + "_cm"]
        new_value = Decimal(token) if token is not None else None
        if cm.get(key) != new_value:
            dimensions_changed = True
            inches = size.setdefault("in", {})
            if new_value is None: inches.pop(key, None)
            else:
                with localcontext() as context:
                    context.prec = 40
                    inches[key] = (new_value / Decimal("2.54")).quantize(Decimal("0.0001"))
        if new_value is None: cm.pop(key, None)
        else: cm[key] = new_value
    if dimensions_changed: size["method"] = "manual" if raw is not None else "database"
    result = (_serialize(data) + "\n").encode("utf-8")
    if len(result) > MAX_BYTES: raise ValueError("SOURCE_METADATA_TOO_LARGE")
    return result
