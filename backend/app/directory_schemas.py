from datetime import date, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from app.schemas import ApiSchema, Name, SearchTerm, Website

CustomerStatus = Literal["In library", "test", "Active"]
OrderStatus = Literal["Test complete", "To be invoiced", "Ongoing", "Canceled", "Done", "Samples Obtained", "Visualize", "Waiting for samples", "Post-production", "Scanned", "Price offer sent", "invoiced", "Not started"]
Priority = Literal["Low", "Medium", "High", "Urgent"]
Number = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}$")]
ProfileText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=20000)]


class CustomerCreate(ApiSchema):
    name: Name
    brand_identifier: Name | None = None
    folder_prefix: Name | None = None
    status: CustomerStatus = "Active"
    website: Website | None = None
    address: ProfileText | None = None
    shipping_address: ProfileText | None = None
    legal_name: Name | None = None
    vat_id: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    description: ProfileText | None = None
    notes: ProfileText | None = None
    is_active: bool = True


class CustomerUpdate(ApiSchema):
    expected_updated_at: datetime
    name: Name | None = None
    brand_identifier: Name | None = None
    status: CustomerStatus | None = None
    website: Website | None = None
    address: ProfileText | None = None
    shipping_address: ProfileText | None = None
    legal_name: Name | None = None
    vat_id: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    description: ProfileText | None = None
    notes: ProfileText | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def nonnull(self) -> Self:
        for key in ("name", "status", "is_active"):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(f"{key} cannot be null")
        return self


class CustomerFilters(ApiSchema):
    search: SearchTerm | None = None
    status: CustomerStatus | None = None
    main_category_code: Name | None = None
    is_active: bool | None = None


class CustomerLogo(ApiSchema):
    expected_updated_at: datetime
    filename: Name
    content_base64: str = Field(min_length=1, max_length=2800000)


class OrderCreate(ApiSchema):
    number: Number | None = None
    customer_id: UUID
    project_type: Name
    starting_date: date
    due_date: date | None = None
    notes: ProfileText | None = None
    responsible_id: UUID | None = None
    status: OrderStatus = "Not started"
    priority: Priority | None = None


class OrderUpdate(ApiSchema):
    expected_updated_at: datetime
    number: Number | None = None
    customer_id: UUID | None = None
    project_type: Name | None = None
    starting_date: date | None = None
    due_date: date | None = None
    notes: ProfileText | None = None
    responsible_id: UUID | None = None
    status: OrderStatus | None = None
    priority: Priority | None = None

    @model_validator(mode="after")
    def nonnull(self) -> Self:
        for key in ("number", "customer_id", "project_type", "starting_date", "status"):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(f"{key} cannot be null")
        return self


class OrderFilters(ApiSchema):
    search: SearchTerm | None = None
    customer_id: UUID | None = None
    status: OrderStatus | None = None
    priority: Priority | None = None
    responsible_id: UUID | None = None
    starting_from: date | None = None
    starting_to: date | None = None
    due_from: date | None = None
    due_to: date | None = None
