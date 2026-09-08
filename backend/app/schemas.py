from datetime import date, datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, model_validator

from app.db.models import (
    InternalUserRole,
    MaterialPublicationStatus,
    MaterialValidationStatus,
    MaterialWorkflowStatus,
    ProjectStatus,
)


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
ShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Website = Annotated[HttpUrl, Field(max_length=2048)]
SearchTerm = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
CategoryCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_upper=True,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9-]+$",
    ),
]
FolderPath = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)
]
EmailAddress = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=3,
        max_length=320,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    ),
]


class ApiSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


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


class CompanyListFilters(ApiSchema):
    search: SearchTerm | None = None
    is_active: bool | None = None


class PublishedBrandFields(ApiSchema):
    company_id: UUID
    name: Name
    folder_prefix: Name
    brand_identifier: Name
    is_active: bool = True


class PublishedBrandCreate(PublishedBrandFields):
    pass


class PublishedBrandUpdate(ApiSchema):
    company_id: UUID | None = None
    name: Name | None = None
    folder_prefix: Name | None = None
    brand_identifier: Name | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def required_fields_cannot_be_null(self) -> Self:
        for field_name in (
            "company_id",
            "name",
            "folder_prefix",
            "brand_identifier",
            "is_active",
        ):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class PublishedBrandRead(PublishedBrandFields):
    id: UUID
    next_sequence_number: int = Field(ge=1, le=10000)
    created_at: datetime
    updated_at: datetime


class PublishedBrandListFilters(ApiSchema):
    company_id: UUID | None = None
    is_active: bool | None = None
    search: SearchTerm | None = None


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


class ProjectListFilters(ApiSchema):
    company_id: UUID | None = None
    status: ProjectStatus | None = None
    search: SearchTerm | None = None


class InternalUserFields(ApiSchema):
    display_name: Name
    email: EmailAddress
    role: InternalUserRole
    is_active: bool = True


class InternalUserCreate(InternalUserFields):
    pass


class InternalUserUpdate(ApiSchema):
    display_name: Name | None = None
    email: EmailAddress | None = None
    role: InternalUserRole | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def required_fields_cannot_be_null(self) -> Self:
        for field_name in ("display_name", "email", "role", "is_active"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class InternalUserRead(InternalUserFields):
    id: UUID
    created_at: datetime
    updated_at: datetime


class InternalUserListFilters(ApiSchema):
    role: InternalUserRole | None = None
    is_active: bool | None = None
    search: SearchTerm | None = None


class PBRMaterialFields(ApiSchema):
    project_id: UUID
    published_brand_id: UUID
    material_name: Name
    main_category_code: CategoryCode
    assigned_processor_id: UUID


class PBRMaterialCreate(PBRMaterialFields):
    pass


class PBRMaterialUpdate(ApiSchema):
    project_id: UUID | None = None
    material_name: Name | None = None
    main_category_code: CategoryCode | None = None
    assigned_processor_id: UUID | None = None

    @model_validator(mode="after")
    def required_fields_cannot_be_null(self) -> Self:
        for field_name in (
            "project_id",
            "material_name",
            "main_category_code",
            "assigned_processor_id",
        ):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class PBRMaterialRead(PBRMaterialFields):
    id: UUID
    sequence_number: int = Field(ge=1, le=9999)
    technical_identity: str
    folder_path: FolderPath | None
    workflow_status: MaterialWorkflowStatus
    validation_status: MaterialValidationStatus
    is_published: bool
    publication_status: MaterialPublicationStatus
    created_at: datetime
    updated_at: datetime


class PBRMaterialListFilters(ApiSchema):
    project_id: UUID | None = None
    published_brand_id: UUID | None = None
    assigned_processor_id: UUID | None = None
    main_category_code: CategoryCode | None = None
    workflow_status: MaterialWorkflowStatus | None = None
    validation_status: MaterialValidationStatus | None = None
    publication_status: MaterialPublicationStatus | None = None
    is_published: bool | None = None
    search: SearchTerm | None = None
