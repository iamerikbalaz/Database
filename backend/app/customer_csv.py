"""Brand CSV matching CSV_BRANDS.csv; export never marks a brand published."""
import csv
import io
import re
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, model_validator
from app.schemas import ApiSchema

COLUMNS = ("brand_identifier", "name", "description", "website", "country")


class CustomerCsvRequest(ApiSchema):
    customer_ids: list[UUID] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def distinct(self):
        if len(set(self.customer_ids)) != len(self.customer_ids):
            raise ValueError("Select each customer only once")
        return self


def export_customers(customers):
    rows, errors, warnings, seen = [], [], [], set()
    for customer in customers:
        identifier = customer.customer_brand_identifier or ""
        # A comma-separated historical mapping is not one brand identifier.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", identifier):
            errors.append(f"{customer.name}: provide one valid Brand identifier.")
        if identifier.casefold() in seen:
            errors.append(f"{customer.name}: duplicate Brand identifier.")
        seen.add(identifier.casefold())
        row = (identifier, customer.name, customer.description or "", customer.website or "", customer.country or "")
        if any(value.lstrip().startswith(("=", "+", "-", "@")) for value in row):
            errors.append(f"{customer.name}: a value starts with a spreadsheet formula character; correct it before export.")
        for field, value in zip(COLUMNS[2:], row[2:]):
            if not value:
                warnings.append(f"{customer.name}: {field} is empty.")
        rows.append(row)
    if errors:
        raise HTTPException(422, {"code": "BRAND_CSV_INCOMPLETE", "message": " ".join(errors)})
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    return {"filename": "CSV_BRANDS.csv", "csv": "\ufeff" + output.getvalue(), "rows": len(rows), "warnings": warnings}
