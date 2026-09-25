"""Explicit import mapping and strict source request models; no writes or guesses."""
import base64
from dataclasses import dataclass, field
from typing import Annotated, Literal
import unicodedata
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError, field_validator, model_validator

from app.import_sources import ImportSourceError, MAX_UPLOAD_BYTES, SourceTable, check_upload, read_csv
from app.import_workbooks import read_xlsx, xlsx_sheets
from app.material_naming import match_identity
from app.inventory_client import validate_relative_path
from app.schemas import Name, CategoryCode, Sha256

MAX_REFERENCE_VALUES = 64
Column = Annotated[str, StringConstraints(min_length=1, max_length=2048)]
NAME = TypeAdapter(Name)
CATEGORY = TypeAdapter(CategoryCode)


class ImportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ImportSource(ImportModel):
    format: Literal["CSV", "XLSX"]
    data: Annotated[str, Field(min_length=1, max_length=4 * ((MAX_UPLOAD_BYTES + 2) // 3), repr=False)]
    delimiter: Literal[",", ";"] | None = None
    sheet: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None

    @model_validator(mode="after")
    def format_options(self):
        if (self.format == "CSV" and (self.delimiter is None or self.sheet is not None)
                or self.format == "XLSX" and self.delimiter is not None):
            raise ValueError("Invalid source options")
        return self

    def decode(self):
        try:
            data = base64.b64decode(self.data, validate=True)
        except (ValueError, UnicodeError):
            raise ImportSourceError("IMPORT_BASE64_INVALID") from None
        check_upload(data)
        return data

    def table(self, data=None):
        data = self.decode() if data is None else data
        return read_csv(data, delimiter=self.delimiter) if self.format == "CSV" else read_xlsx(data, sheet=self.sheet)

    def options(self):
        return self.model_dump(exclude={"data"})


class ImportColumns(ImportModel):
    identity: Column
    name: Column
    project: Column | None = None
    brand: Column
    processor: Column
    folder: Column | None = None

    @model_validator(mode="after")
    def distinct(self):
        headers = [value for value in self.model_dump().values() if value is not None]
        if len(set(headers)) != len(headers):
            raise ValueError("Choose distinct source columns")
        return self


def mapping_key(value):
    return unicodedata.normalize("NFC", value).strip()


class ImportLinks(ImportModel):
    projects: Annotated[dict[str, UUID], Field(max_length=MAX_REFERENCE_VALUES)]
    brands: Annotated[dict[str, UUID], Field(max_length=MAX_REFERENCE_VALUES)]
    processors: Annotated[dict[str, UUID], Field(max_length=MAX_REFERENCE_VALUES)]

    @field_validator("projects", "brands", "processors")
    @classmethod
    def normalize_keys(cls, values):
        normalized = {}
        for key, value in values.items():
            clean = mapping_key(key)
            if (not clean or len(clean) > 2048 or clean in normalized
                    or any(unicodedata.category(char).startswith("C") for char in clean)):
                raise ValueError("Invalid or ambiguous source mapping")
            normalized[clean] = value
        return normalized


class InspectImport(ImportModel):
    source: ImportSource
    columns: ImportColumns | None = None


class PlanImport(ImportModel):
    source: ImportSource
    columns: ImportColumns
    links: ImportLinks

    @model_validator(mode="after")
    def no_unused_project_mapping(self):
        if self.columns.project is None and self.links.projects:
            raise ValueError("An import without a project column must not map projects")
        return self


class ConfirmImport(PlanImport):
    idempotency_key: UUID
    expected_preview_hash: Sha256
    acknowledge_unverified: Literal[True]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]

    @field_validator("acknowledge_unverified", mode="before")
    @classmethod
    def explicit_acknowledgement(cls, value):
        if value is not True:
            raise ValueError("Explicit acknowledgement required")
        return value

    @field_validator("reason")
    @classmethod
    def safe_reason(cls, value):
        if any(unicodedata.category(char).startswith("C") and char not in "\t\r\n" for char in value):
            raise ValueError("Invalid import reason")
        return unicodedata.normalize("NFC", value)


def column_indices(table, columns):
    positions = {}
    for name, header in columns.model_dump().items():
        if header is None:
            continue
        if header not in table.headers:
            raise ImportSourceError("IMPORT_COLUMN_MAPPING")
        positions[name] = table.headers.index(header)
    return positions


def inspect_source(payload: InspectImport):
    data = payload.source.decode()
    sheets = xlsx_sheets(data) if payload.source.format == "XLSX" else ()
    if sheets and payload.source.sheet is None:
        return {"format": payload.source.format, "sheets": sheets, "requires_sheet": True}
    table = payload.source.table(data)
    result = {"format": payload.source.format, "sheets": sheets, "requires_sheet": False,
              "source_sha256": table.source_sha256, "headers": table.headers, "row_count": len(table.rows),
              "sample": [{"row": row.number, "values": row.values} for row in table.rows[:10]]}
    if payload.columns is not None:
        positions = column_indices(table, payload.columns)
        values = {name: sorted({mapping_key(row.values[positions[name]]) for row in table.rows}) if name in positions else []
                  for name in ("project", "brand", "processor")}
        if any(len(group) > MAX_REFERENCE_VALUES for group in values.values()):
            raise ImportSourceError("IMPORT_REFERENCE_LIMIT")
        if any(not value or any(unicodedata.category(char).startswith("C") for char in value)
               for group in values.values() for value in group):
            raise ImportSourceError("IMPORT_REFERENCE_LABEL")
        result["mapping_values"] = values
    return result


@dataclass(frozen=True)
class PreparedImportRow:
    source_row: int
    technical_identity: str = field(repr=False)
    material_name: str = field(repr=False)
    prefix: str = field(repr=False)
    sequence_number: int
    main_category_code: str
    project_id: UUID | None
    brand_id: UUID
    processor_id: UUID
    folder_path: str | None = field(default=None, repr=False)

    def public_values(self):
        return {"source_row": self.source_row, "technical_identity": self.technical_identity,
                "material_name": self.material_name, "sequence_number": self.sequence_number,
                "main_category_code": self.main_category_code, "project_id": str(self.project_id) if self.project_id is not None else None,
                "published_brand_id": str(self.brand_id), "assigned_processor_id": str(self.processor_id),
                "folder_path": self.folder_path}


def prepare_rows(table: SourceTable, columns: ImportColumns, links: ImportLinks):
    positions = column_indices(table, columns)
    prepared = []
    findings = []
    seen_identities, seen_numbers = {}, {}
    for row in table.rows:
        start = len(findings)
        def issue(field, code):
            findings.append({"row": row.number, "field": field, "code": code})
        identity = row.values[positions["identity"]].strip()
        match = match_identity(identity)
        prefix, number, category = (match["prefix"], match["number"], match["category"]) if match else ("", "", "")
        if match is None:
            issue("identity", "IMPORT_IDENTITY_FORMAT")
        try:
            if CATEGORY.validate_python(category) != category:
                raise ValueError()
        except (ValidationError, ValueError):
            issue("identity", "IMPORT_CATEGORY_FORMAT")
        try:
            name = NAME.validate_python(row.values[positions["name"]])
        except ValidationError:
            name = ""
            issue("name", "IMPORT_MATERIAL_NAME")
        references = {}
        folder = None
        if "folder" in positions:
            folder = row.values[positions["folder"]].strip()
            try:
                if len(folder) > 2048:
                    raise ValueError()
                validate_relative_path(folder)
                if folder.rsplit("/", 1)[-1] != identity:
                    raise ValueError()
            except ValueError:
                issue("identity", "IMPORT_FOLDER_REFERENCE_INVALID")
        for group, mapping in (("project", links.projects), ("brand", links.brands), ("processor", links.processors)):
            if group == "project" and group not in positions:
                references[group] = None
                continue
            key = mapping_key(row.values[positions[group]])
            identifier = mapping.get(key)
            if identifier is None:
                issue(group, "IMPORT_REFERENCE_UNMAPPED")
            else:
                references[group] = identifier
        if len(findings) != start:
            continue
        item = PreparedImportRow(row.number, identity, name, prefix, int(number), category,
                                 references["project"], references["brand"], references["processor"], folder)
        if identity in seen_identities or (item.brand_id, item.sequence_number) in seen_numbers:
            issue("identity", "IMPORT_DUPLICATE_IDENTITY_OR_NUMBER")
            continue
        seen_identities[identity] = row.number
        seen_numbers[item.brand_id, item.sequence_number] = row.number
        prepared.append(item)
    return prepared, findings
