"""Inert XLSX values with bounded ZIP/XML parsing. Never extracts or evaluates."""
import io
import re
import stat
import unicodedata
from xml.etree.ElementTree import ParseError
import zipfile

from defusedxml.ElementTree import iterparse
from defusedxml.common import DefusedXmlException

from app.import_sources import (
    ImportSourceError, MAX_CELL_CHARS, MAX_COLUMNS, MAX_ROWS, build_table,
    check_upload, checked_cell,
)

S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
P = "{http://schemas.openxmlformats.org/package/2006/relationships}"
C = "{http://schemas.openxmlformats.org/package/2006/content-types}"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
MAX_PARTS = 128
MAX_PART_BYTES = 8 * 1024**2
MAX_EXPANDED_BYTES = 16 * 1024**2
MAX_XML_NODES = 250_000
MAX_XML_DEPTH = 64
MAX_SHEETS = 16


def _part_name(name):
    return (bool(name) and len(name) <= 255 and not name.startswith("/")
            and not any(unicodedata.category(c).startswith("C") or c in "\\:%" for c in name)
            and all(part not in {"", ".", ".."} for part in name.split("/")))


def _xml(data, budget):
    depth = 0
    parser = iterparse(io.BytesIO(data), events=("start", "end"),
                       forbid_dtd=True, forbid_entities=True, forbid_external=True)
    for event, element in parser:
        if event == "start":
            depth += 1
            budget[0] += 1
            if depth > MAX_XML_DEPTH or budget[0] > MAX_XML_NODES or len(element.attrib) > 32:
                raise ImportSourceError("IMPORT_XML_LIMIT")
            if element.tag == S + "f":
                raise ImportSourceError("IMPORT_FORMULA")
            if element.tag == P + "Relationship" and element.get("TargetMode", "Internal") != "Internal":
                raise ImportSourceError("IMPORT_EXTERNAL_LINK")
            if element.tag.startswith("{http://www.w3.org/2001/XInclude}"):
                raise ImportSourceError("IMPORT_EXTERNAL_LINK")
        else:
            depth -= 1
    return parser.root


def _package(data):
    check_upload(data)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if not 1 <= len(entries) <= MAX_PARTS:
                raise ImportSourceError("IMPORT_ZIP_LIMIT")
            names = set()
            expanded = 0
            for entry in entries:
                name = entry.filename.rstrip("/") if entry.is_dir() else entry.filename
                if not _part_name(name) or entry.orig_filename != entry.filename or name.casefold() in names:
                    raise ImportSourceError("IMPORT_ZIP_ENTRY")
                names.add(name.casefold())
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if (entry.flag_bits & 1 or mode not in {0, stat.S_IFREG, stat.S_IFDIR}
                        or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                        or (entry.is_dir() and entry.file_size)):
                    raise ImportSourceError("IMPORT_ZIP_ENTRY")
                if (name.lower().endswith(".bin") or any(part in name.lower() for part in
                    ("externallinks/", "embeddings/", "activex/", "connections.xml", "macrosheets/"))):
                    raise ImportSourceError("IMPORT_ACTIVE_CONTENT")
                expanded += entry.file_size
                if entry.file_size > MAX_PART_BYTES or expanded > MAX_EXPANDED_BYTES:
                    raise ImportSourceError("IMPORT_ZIP_LIMIT")
            parts = {}
            budget = [0]
            for entry in entries:
                if entry.is_dir() or not entry.filename.lower().endswith((".xml", ".rels")):
                    continue
                with archive.open(entry) as stream:
                    raw = stream.read(MAX_PART_BYTES + 1)
                if len(raw) != entry.file_size or len(raw) > MAX_PART_BYTES:
                    raise ImportSourceError("IMPORT_ZIP_LIMIT")
                parts[entry.filename] = _xml(raw, budget)
        types = parts.get("[Content_Types].xml")
        if types is None or types.tag != C + "Types":
            raise ImportSourceError("IMPORT_XLSX_STRUCTURE")
        for item in types:
            content_type = item.get("ContentType", "").lower()
            if any(value in content_type for value in ("macroenabled", "vbaproject", "macrosheet")):
                raise ImportSourceError("IMPORT_ACTIVE_CONTENT")
        root_links = parts.get("_rels/.rels")
        office = [] if root_links is None else [item for item in root_links if item.get("Type") == REL + "officeDocument"]
        if len(office) != 1 or office[0].get("Target") not in {"xl/workbook.xml", "/xl/workbook.xml"}:
            raise ImportSourceError("IMPORT_XLSX_STRUCTURE")
        return parts
    except ImportSourceError:
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError, ParseError, DefusedXmlException):
        raise ImportSourceError("IMPORT_XLSX_INVALID") from None


def _sheets(parts):
    workbook, relationships = parts.get("xl/workbook.xml"), parts.get("xl/_rels/workbook.xml.rels")
    if workbook is None or workbook.tag != S + "workbook" or relationships is None or relationships.tag != P + "Relationships":
        raise ImportSourceError("IMPORT_XLSX_STRUCTURE")
    links = {}
    for item in relationships:
        identifier = item.get("Id")
        if not identifier or identifier in links:
            raise ImportSourceError("IMPORT_XLSX_STRUCTURE")
        links[identifier] = item
    sheets = workbook.find(S + "sheets")
    if sheets is None or not 1 <= len(sheets) <= MAX_SHEETS:
        raise ImportSourceError("IMPORT_SHEET_LIMIT")
    result = {}
    targets = set()
    for item in sheets:
        name = item.get("name", "")
        if not name or len(name) > 64 or any(unicodedata.category(c).startswith("C") for c in name):
            raise ImportSourceError("IMPORT_XLSX_STRUCTURE")
        link = links.get(item.get(R + "id"))
        if item.tag != S + "sheet" or link is None or link.get("Type") != REL + "worksheet":
            raise ImportSourceError("IMPORT_XLSX_STRUCTURE")
        target = link.get("Target", "")
        target = target[1:] if target.startswith("/") else "xl/" + target
        if (not _part_name(target) or not target.startswith("xl/worksheets/") or target not in parts
                or target in targets or name.casefold() in {value.casefold() for value in result}):
            raise ImportSourceError("IMPORT_XLSX_STRUCTURE")
        targets.add(target)
        result[name] = target
    return result


def xlsx_sheets(data: bytes) -> tuple[str, ...]:
    return tuple(_sheets(_package(data)))


def _rich_text(element):
    if element is None:
        return ""
    # Phonetic annotations and formatting are not part of the literal cell value.
    values = []
    for child in element:
        if child.tag == S + "t":
            values.append(child.text or "")
        elif child.tag == S + "r":
            values.extend(text.text or "" for text in child.findall(S + "t"))
    value = "".join(values)
    if len(value) > MAX_CELL_CHARS:
        raise ImportSourceError("IMPORT_CELL_SIZE")
    return value


def _shared_strings(parts):
    root = parts.get("xl/sharedStrings.xml")
    if root is None:
        return ()
    if root.tag != S + "sst" or len(root) > (MAX_ROWS + 1) * MAX_COLUMNS:
        raise ImportSourceError("IMPORT_XLSX_STRUCTURE")
    values = []
    for item in root:
        if item.tag != S + "si":
            raise ImportSourceError("IMPORT_XLSX_STRUCTURE")
        values.append(_rich_text(item))
    return values


def _value(cell, strings, row, column):
    kind = cell.get("t", "n")
    values = cell.findall(S + "v")
    if (len(values) > 1 or any(child.tag not in {S + "v", S + "is"} for child in cell)
            or any(len(value) for value in values) or (kind != "inlineStr" and cell.find(S + "is") is not None)):
        raise ImportSourceError("IMPORT_XLSX_CELL", row, column)
    value = (values[0].text or "") if values else ""
    if kind == "inlineStr":
        if values or len(cell.findall(S + "is")) > 1:
            raise ImportSourceError("IMPORT_XLSX_CELL", row, column)
        value = _rich_text(cell.find(S + "is"))
    elif kind == "s":
        if not re.fullmatch(r"[0-9]{1,6}", value) or int(value) >= len(strings):
            raise ImportSourceError("IMPORT_XLSX_CELL", row, column)
        value = strings[int(value)]
    elif kind == "b":
        if value not in {"0", "1"}:
            raise ImportSourceError("IMPORT_XLSX_CELL", row, column)
        value = "TRUE" if value == "1" else "FALSE"
    elif kind not in {"n", "str", "d"}:
        raise ImportSourceError("IMPORT_XLSX_CELL", row, column)
    elif kind == "n" and value and not re.fullmatch(r"-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]+)?", value):
        raise ImportSourceError("IMPORT_XLSX_CELL", row, column)
    return checked_cell(value, row, column)


def read_xlsx(data: bytes, *, sheet: str):
    parts = _package(data)
    sheets = _sheets(parts)
    if not isinstance(sheet, str) or sheet not in sheets:
        raise ImportSourceError("IMPORT_SHEET_REQUIRED")
    strings = _shared_strings(parts)
    root = parts[sheets[sheet]]
    if root.tag != S + "worksheet" or root.find(S + "mergeCells") is not None:
        raise ImportSourceError("IMPORT_XLSX_STRUCTURE")
    contents = root.findall(S + "sheetData")
    if len(contents) != 1:
        raise ImportSourceError("IMPORT_XLSX_STRUCTURE")

    def records():
        previous = 0
        width = None
        for element in contents[0]:
            raw = element.get("r", "")
            if element.tag != S + "row" or not re.fullmatch(r"[1-9][0-9]{0,6}", raw):
                raise ImportSourceError("IMPORT_XLSX_STRUCTURE")
            number = int(raw)
            if number > MAX_ROWS + 1:
                raise ImportSourceError("IMPORT_ROW_LIMIT", number)
            if number <= previous:
                raise ImportSourceError("IMPORT_XLSX_STRUCTURE", number)
            previous = number
            cells = {}
            for cell in element:
                reference = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,6})", cell.get("r", ""))
                if cell.tag != S + "c" or reference is None or int(reference[2]) != number:
                    raise ImportSourceError("IMPORT_XLSX_CELL", number)
                column = 0
                for letter in reference[1]:
                    column = column * 26 + ord(letter) - ord("A") + 1
                if column > MAX_COLUMNS:
                    raise ImportSourceError("IMPORT_COLUMN_LIMIT", number, column)
                if column in cells:
                    raise ImportSourceError("IMPORT_XLSX_CELL", number, column)
                cells[column] = _value(cell, strings, number, column)
            if width is None:
                width = max(cells, default=0)
            if max(cells, default=0) > width:
                raise ImportSourceError("IMPORT_ROW_WIDTH", number)
            yield number, [cells.get(column, "") for column in range(1, width + 1)]

    return build_table(data, records())
