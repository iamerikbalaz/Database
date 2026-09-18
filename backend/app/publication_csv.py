"""Pure serialization of frozen publication inputs; no DB, IO or authorization.

The later batch service must prove matching human approvals before freezing rows.
A CSV artifact never proves successful packaging, upload or online publication.
"""
import csv
import hashlib
import io
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, field_validator

from app.catalog import CatalogValue, ContentUpdate, TagValue, value_key
from app.material_review import canonical_hash

CSV_COLUMNS = ("identity_name", "name", "description", "credits", "dimension",
    "brand_identifier", "categories", "color", "tags")
MAX_BATCH_SIZE = 100
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


def plain_name(value):
    if (not isinstance(value, str) or not value.strip()
            or any(unicodedata.category(char).startswith("C") for char in value)):
        raise ValueError("Invalid publication name")
    return value


Name = Annotated[str, StringConstraints(min_length=1, max_length=255), BeforeValidator(plain_name)]


def exact_centimetres(value):
    # Never convert binary floats or round a value outside the database contract.
    if not isinstance(value, (str, Decimal)) or (isinstance(value, str) and len(value) > 64):
        raise ValueError("Exact decimal centimetres required")
    try:
        number = Decimal(value)
        if not number.is_finite() or not Decimal("0") < number <= Decimal("99999999.9999"):
            raise ValueError()
        parts = number.as_tuple()
        # Decimal arithmetic can itself round to the current precision. Inspect
        # exact coefficient digits instead, including fractions beyond 28 places.
        if len(parts.digits) > 64 or (parts.exponent < -4 and any(parts.digits[parts.exponent + 4:])):
            raise ValueError()
        return Decimal(dimension_text(number))
    except (InvalidOperation, ValueError):
        raise ValueError("Unsupported publication dimension") from None


Centimetres = Annotated[Decimal, BeforeValidator(exact_centimetres)]


class PublicationCsvRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    material_id: UUID
    revision_hash: Digest
    content_context_hash: Digest
    identity_name: Annotated[str, StringConstraints(min_length=1, max_length=512), BeforeValidator(plain_name)]
    name: Name
    description: Annotated[str, StringConstraints(max_length=10000)] | None
    credits: Annotated[int, Field(strict=True, ge=0, le=2147483647)]
    width_cm: Centimetres
    height_cm: Centimetres
    brand_identifier: Name
    categories: Annotated[tuple[CatalogValue, ...], Field(min_length=1, max_length=100)]
    color: Annotated[str, StringConstraints(pattern=r"^#[0-9A-F]{6}$")]
    tags: Annotated[tuple[TagValue, ...], Field(max_length=100)]

    @field_validator("description")
    @classmethod
    def description_text(cls, value):
        return ContentUpdate.plain_description(value)

    @field_validator("categories", "tags")
    @classmethod
    def individual_values(cls, values):
        # Keep the first display spelling, as the content/catalog API does.
        unique = {}
        for value in values: unique.setdefault(value_key(value), value)
        return tuple(unique[key] for key in sorted(unique))


@dataclass(frozen=True)
class PublicationRowDigest:
    material_id: UUID
    revision_hash: str
    content_context_hash: str
    snapshot_hash: str


@dataclass(frozen=True)
class PublicationCsvWarning:
    material_id: UUID
    code: str
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class PublicationCsv:
    data: bytes
    sha256: str
    rows: tuple[PublicationRowDigest, ...]
    warnings: tuple[PublicationCsvWarning, ...]


def dimension_text(value: Decimal) -> str:
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def render_publication_csv(rows: tuple[PublicationCsvRow, ...] | list[PublicationCsvRow]) -> PublicationCsv:
    if not 1 <= len(rows) <= MAX_BATCH_SIZE or any(not isinstance(row, PublicationCsvRow) for row in rows):
        raise ValueError("A bounded set of validated publication snapshots is required")
    if (len({row.material_id for row in rows}) != len(rows)
            or len({value_key(row.identity_name) for row in rows}) != len(rows)):
        raise ValueError("Duplicate publication material or identity")
    ordered = sorted(rows, key=lambda row: (value_key(row.identity_name), str(row.material_id)))
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", quotechar='"', lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(CSV_COLUMNS)
    digests = []
    warnings = []
    for row in ordered:
        values = (row.identity_name, row.name, row.description or "", str(row.credits),
            f"{dimension_text(row.width_cm)}x{dimension_text(row.height_cm)} cm", row.brand_identifier,
            ":".join(row.categories), row.color, ":".join(row.tags))
        writer.writerow(values)
        digests.append(PublicationRowDigest(row.material_id, row.revision_hash, row.content_context_hash,
            canonical_hash(row.model_dump(mode="json"))))
        if not row.description or not row.description.strip():
            warnings.append(PublicationCsvWarning(row.material_id, "CONTENT_DESCRIPTION_EMPTY"))
        if not row.tags: warnings.append(PublicationCsvWarning(row.material_id, "CONTENT_TAGS_EMPTY"))
        formula_fields = tuple(name for name, value in zip(CSV_COLUMNS, values, strict=True)
            if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")))
        if formula_fields:
            # Escaping with an apostrophe would change the legacy importer value.
            # Surface a warning; do not silently modify approved publication text.
            warnings.append(PublicationCsvWarning(row.material_id, "CSV_FORMULA_LIKE_VALUE", formula_fields))
    data = output.getvalue().encode("utf-8-sig")
    return PublicationCsv(data, hashlib.sha256(data).hexdigest(), tuple(digests), tuple(warnings))
