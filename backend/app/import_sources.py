"""Bounded, inert source tables for historical imports; no database or filesystem IO."""
import csv
from dataclasses import dataclass, field
import hashlib
import io
import unicodedata

MAX_UPLOAD_BYTES = 4 * 1024**2
MAX_ROWS = 2000
MAX_COLUMNS = 32
MAX_CELL_CHARS = 2048


class ImportSourceError(ValueError):
    """Safe coordinates and a fixed code, never uploaded values or parser messages."""

    def __init__(self, code: str, row: int | None = None, column: int | None = None):
        self.code, self.row, self.column = code, row, column
        super().__init__(code)


@dataclass(frozen=True)
class SourceRow:
    number: int
    values: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True)
class SourceTable:
    source_sha256: str
    headers: tuple[str, ...] = field(repr=False)
    rows: tuple[SourceRow, ...] = field(repr=False)


def check_upload(data: bytes) -> None:
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_UPLOAD_BYTES:
        raise ImportSourceError("IMPORT_FILE_SIZE")


def checked_cell(value: str, row: int, column: int) -> str:
    if len(value) > MAX_CELL_CHARS:
        raise ImportSourceError("IMPORT_CELL_SIZE", row, column)
    if any(unicodedata.category(char).startswith("C") and char not in "\t\r\n" for char in value):
        raise ImportSourceError("IMPORT_CELL_CONTROL", row, column)
    # Import only literal values. CSV formula prefixes are also rejected even
    # though this reader never evaluates them or launches a spreadsheet program.
    if value.lstrip().startswith(("=", "+", "-", "@")):
        raise ImportSourceError("IMPORT_FORMULA", row, column)
    return value


def build_table(data: bytes, records) -> SourceTable:
    headers = None
    rows = []
    count = 0
    for number, values in records:
        count += 1
        if count > MAX_ROWS + 1:
            raise ImportSourceError("IMPORT_ROW_LIMIT", number)
        if not values and headers is not None:
            values = ("",) * len(headers)
        if not 1 <= len(values) <= MAX_COLUMNS:
            raise ImportSourceError("IMPORT_COLUMN_LIMIT", number)
        values = tuple(checked_cell(value, number, index + 1) for index, value in enumerate(values))
        if headers is None:
            headers = tuple(unicodedata.normalize("NFC", value).strip() for value in values)
            if any(not value or "\n" in value or "\r" in value or "\t" in value for value in headers):
                raise ImportSourceError("IMPORT_HEADER", number)
            if len({value.casefold() for value in headers}) != len(headers):
                raise ImportSourceError("IMPORT_DUPLICATE_HEADER", number)
        elif len(values) != len(headers):
            raise ImportSourceError("IMPORT_ROW_WIDTH", number)
        elif any(value.strip() for value in values):
            rows.append(SourceRow(number, values))
    if headers is None or not rows:
        raise ImportSourceError("IMPORT_EMPTY_TABLE")
    return SourceTable(hashlib.sha256(data).hexdigest(), headers, tuple(rows))


def read_csv(data: bytes, *, delimiter: str) -> SourceTable:
    check_upload(data)
    if delimiter not in {",", ";"}:
        raise ImportSourceError("IMPORT_DELIMITER")
    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeError:
        raise ImportSourceError("IMPORT_UTF8_REQUIRED") from None
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)

    def records():
        while True:
            number = reader.line_num + 1
            try:
                values = next(reader)
            except StopIteration:
                return
            yield number, values

    try:
        return build_table(data, records())
    except csv.Error:
        raise ImportSourceError("IMPORT_CSV_INVALID", reader.line_num or 1) from None
