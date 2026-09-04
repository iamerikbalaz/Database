from datetime import date, datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, model_validator

from app.db.models import ProjectStatus


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
ShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Website = Annotated[HttpUrl, Field(max_length=2048)]


class ApiSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CompanyFields(ApiSchema):
    name: Name
    legal_name: Name | None = None
    country: ShortText | None = None
    address: LongText | None = None
    website: Website | None = None
    vat_id: ShortText | None = None
    notion_page_id: Name | None = None
    is_active: bool = True


class CompanyCreate(CompanyFields):
    pass


class CompanyUpdate(ApiSchema):
    name: Name | None = None
    legal_name: Name | None = None
    country: ShortText | None = None
    address: LongText | None = None
    website: Website | None = None
    vat_id: ShortText | None = None
    notion_page_id: Name | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def required_fields_cannot_be_null(self) -> Self:
        for field_name in ("name", "is_active"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class CompanyRead(CompanyFields):
    id: UUID
    created_at: datetime
    updated_at: datetime


class PublishedBrandFields(ApiSchema):
    company_id: UUID
    name: Name
    folder_prefix: Name
    brand_identifier: Name
    next_sequence_number: int = Field(default=1, ge=1)
    is_active: bool = True


class PublishedBrandCreate(PublishedBrandFields):
    pass


class PublishedBrandUpdate(ApiSchema):
    company_id: UUID | None = None
    name: Name | None = None
    folder_prefix: Name | None = None
    brand_identifier: Name | None = None
    next_sequence_number: int | None = Field(default=None, ge=1)
    is_active: bool | None = None

    @model_validator(mode="after")
    def required_fields_cannot_be_null(self) -> Self:
        for field_name in (
            "company_id",
            "name",
            "folder_prefix",
            "brand_identifier",
            "next_sequence_number",
            "is_active",
        ):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class PublishedBrandRead(PublishedBrandFields):
    id: UUID
    created_at: datetime
    updated_at: datetime


class ProjectFields(ApiSchema):
    company_id: UUID
    project_number: ShortText
    name: Name
    status: ProjectStatus = ProjectStatus.NOT_STARTED
    due_date: date | None = None
    notes: LongText | None = None


class ProjectCreate(ProjectFields):
    pass


class ProjectUpdate(ApiSchema):
    company_id: UUID | None = None
    project_number: ShortText | None = None
    name: Name | None = None
    status: ProjectStatus | None = None
    due_date: date | None = None
    notes: LongText | None = None

    @model_validator(mode="after")
    def required_fields_cannot_be_null(self) -> Self:
        for field_name in ("company_id", "project_number", "name", "status"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class ProjectRead(ProjectFields):
    id: UUID
    created_at: datetime
    updated_at: datetime
