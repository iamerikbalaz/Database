"""Explicit spreadsheet properties. Units without a suffix are centimetres."""
from decimal import Decimal
import re
import unicodedata

PROPERTY_COLUMNS = ("color", "sample_size", "done", "checked", "note", "brand_identifier")


def parse_properties(values):
    properties, findings = {}, []
    def issue(field, code): findings.append((field, code))
    if "color" in values:
        color = values["color"].strip()
        if color and not re.fullmatch(r"#?[0-9A-Fa-f]{6}", color): issue("color", "IMPORT_COLOR_INVALID")
        else: properties["hex_color"] = "#" + color.lstrip("#").upper() if color else None
    if "sample_size" in values:
        size = values["sample_size"].strip()
        dimensions = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*[xX×]\s*(\d+(?:[.,]\d+)?)(?:\s*-?\s*cm)?", size, re.IGNORECASE)
        if not size: properties.update(width_cm=None, height_cm=None)
        elif dimensions:
            parsed = [Decimal(part.replace(",", ".")) for part in dimensions.groups()]
            if any(value <= 0 or value >= 100_000_000 or value.as_tuple().exponent < -4 for value in parsed):
                issue("sample_size", "IMPORT_SAMPLE_SIZE_INVALID")
            else: properties.update(zip(("width_cm", "height_cm"), (format(value, "f") for value in parsed)))
        else: issue("sample_size", "IMPORT_SAMPLE_SIZE_INVALID")
    if "done" in values:
        value = values["done"].strip().casefold()
        if value not in {"", "no", "yes", "false", "true", "0", "1"}: issue("done", "IMPORT_DONE_INVALID")
        else: properties["workflow_status"] = "DONE" if value in {"yes", "true", "1"} else "IN_PROGRESS"
    if "checked" in values:
        value = values["checked"].strip().casefold()
        checked = {"": "no", "no": "no", "false": "no", "0": "no", "yes": "OK", "true": "OK", "1": "OK", "ok": "OK", "correction": "Correction"}
        if value not in checked: issue("checked", "IMPORT_CHECKED_INVALID")
        else: properties["checked_status"] = checked[value]
        if properties.get("checked_status") == "OK" and properties.get("workflow_status") != "DONE":
            issue("checked", "IMPORT_CHECKED_REQUIRES_DONE")
        if properties.get("checked_status") == "Correction" and properties.get("workflow_status") == "DONE":
            issue("checked", "IMPORT_CORRECTION_REQUIRES_IN_PROGRESS")
    if "note" in values:
        note = unicodedata.normalize("NFC", values["note"]).strip()
        if len(note) > 10_000 or any(unicodedata.category(char).startswith("C") and char not in "\r\n\t" for char in note):
            issue("note", "IMPORT_NOTE_INVALID")
        else: properties["note"] = note or None
    if "brand_identifier" in values:
        identifier = unicodedata.normalize("NFC", values["brand_identifier"]).strip()
        if not identifier or len(identifier) > 255 or any(unicodedata.category(char).startswith("C") for char in identifier):
            issue("brand_identifier", "IMPORT_BRAND_IDENTIFIER_INVALID")
        else: properties["brand_identifier"] = identifier
    return properties, findings
