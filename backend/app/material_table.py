"""Human production tracking. Technical validation remains independent."""
from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from uuid import UUID

from app.schemas import ApiSchema, CategoryCode

Checked = Literal["no", "OK", "Correction"]


class MaterialTableUpdate(ApiSchema):
    expected_updated_at: datetime
    project_id: UUID | None = None
    assigned_processor_id: UUID | None = None
    main_category_code: CategoryCode | None = None
    published_brand_id: UUID | None = None
    workflow_status: Literal["IN_PROGRESS", "DONE"] | None = None
    checked_status: Checked | None = None
    is_published: bool | None = None
    note: Annotated[str, Field(max_length=10000)] | None = None

    @model_validator(mode="after")
    def one_cell(self) -> Self:
        fields = self.model_fields_set - {"expected_updated_at"}
        if len(fields) != 1:
            raise ValueError("Submit exactly one changed property.")
        field = next(iter(fields))
        if field not in {"project_id", "note"} and getattr(self, field) is None:
            raise ValueError(f"{field} cannot be null")
        return self


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
