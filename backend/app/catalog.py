"""Canonical individual publication values; separators belong only to export."""
import unicodedata
from typing import Annotated
from uuid import UUID

from pydantic import BeforeValidator, Field, StringConstraints, field_validator

from app.schemas import ApiSchema


def normalize_value(value):
    if not isinstance(value, str):
        raise ValueError("A catalog value must be text.")
    # Reject invisible/control data before collapsing ordinary whitespace.
    if any(unicodedata.category(char).startswith("C") for char in value) or ":" in value:
        raise ValueError("A single catalog value cannot contain colons or control characters.")
    return unicodedata.normalize("NFC", " ".join(value.split()))


def value_key(value):
    return unicodedata.normalize("NFC", value.casefold())


CatalogValue = Annotated[str, StringConstraints(min_length=1, max_length=255), BeforeValidator(normalize_value)]
TagValue = Annotated[str, StringConstraints(min_length=1, max_length=100), BeforeValidator(normalize_value)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class CategoryCreate(ApiSchema):
    idempotency_key: UUID
    value: CatalogValue


class CollectionCreate(CategoryCreate):
    brand_id: UUID


class CatalogActivityUpdate(ApiSchema):
    idempotency_key: UUID
    expected_version: Annotated[int, Field(strict=True, ge=1)]
    is_active: Annotated[bool, Field(strict=True)]
    reason: Reason


class ContentUpdate(ApiSchema):
    idempotency_key: UUID
    expected_revision: Annotated[int, Field(strict=True, ge=0)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=10000)] | None = None
    credits: Annotated[int, Field(strict=True, ge=0, le=2147483647)] | None = None
    tags: Annotated[list[TagValue], Field(max_length=100)] = []
    category_ids: Annotated[list[UUID], Field(max_length=100)] = []
    collection_ids: Annotated[list[UUID], Field(max_length=100)] = []
    reason: Reason

    @field_validator("tags")
    @classmethod
    def canonical_tags(cls, values):
        unique = {}
        for value in values:
            unique.setdefault(value_key(value), value)
        return [unique[key] for key in sorted(unique)]

    @field_validator("category_ids", "collection_ids")
    @classmethod
    def canonical_ids(cls, values):
        return sorted(set(values))

    @field_validator("description")
    @classmethod
    def plain_description(cls, value):
        if value and any(unicodedata.category(char).startswith("C") and char not in "\n\r\t" for char in value):
            raise ValueError("Description contains unsupported control characters.")
        return unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n") if value else None
