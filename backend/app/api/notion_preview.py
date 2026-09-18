"""Administrator-only comparison of an existing company's explicit Notion link."""
from functools import partial
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from app.auth.access import ADMIN, AccessDependency
from app.db.models import Company
from app.material_review import canonical_hash
from app.notion_reader import (CompanyObservation, NotionError, configuration_from_settings, mapping_hash, page_id)
from app.schemas import ApiSchema


class NotionPreview(ApiSchema):
    expected_page_id: str = Field(min_length=32, max_length=36, strict=True)


def _context(database, identifier, access, fields):
    with database.session() as session:
        access.check(session, ADMIN)
        company = session.get(Company, identifier)
        if company is None: raise HTTPException(404, "Company not found.")
        try: linked = page_id(company.notion_page_id)
        except NotionError: raise HTTPException(409, {"code": "NOTION_LINK_REQUIRED"}) from None
        snapshot = {field: getattr(company, field) for field in (
            "name", "legal_name", "country", "address", "website", "vat_id", "notion_page_id", "is_active")}
        snapshot["id"] = str(company.id); snapshot["updated_at"] = company.updated_at.isoformat()
        return {"page_id": linked, "local_sha256": canonical_hash(snapshot), "values": {field: snapshot[field] for field in fields}}


def build_notion_preview_router(database, reader, settings):
    router = APIRouter(tags=["Notion comparison"])

    @router.get("/api/integrations/notion")
    def configuration(access: AccessDependency):
        with database.session() as session: access.check(session, ADMIN)
        options = configuration_from_settings(settings)
        return {"enabled": options.enabled, "direction": "READ_ONLY", "resource": "COMPANY",
            "mapped_fields": [item.field for item in options.properties]}

    @router.post("/api/companies/{company_id}/notion-preview")
    async def preview(company_id: UUID, payload: NotionPreview, access: AccessDependency):
        options = configuration_from_settings(settings)
        fields = tuple(sorted(item.field for item in options.properties))
        load = partial(_context, database, company_id, access, fields)
        before = await run_in_threadpool(load)
        try: expected = page_id(payload.expected_page_id)
        except NotionError: raise HTTPException(422, {"code": "NOTION_SELECTION_INVALID"}) from None
        if expected != before["page_id"]: raise HTTPException(409, {"code": "NOTION_LINK_CHANGED"})
        if not options.enabled: raise HTTPException(503, {"code": "NOTION_DISABLED"})
        # Only deployment configuration can select the source and projection.
        if reader.configuration != options: raise HTTPException(503, {"code": "NOTION_CONFIGURATION_INVALID"})
        async def guard():
            if await run_in_threadpool(load) != before:
                raise HTTPException(409, {"code": "NOTION_LOCAL_CHANGED"})
        error = None; observed = None
        try:
            response = await reader.company(expected, operation_guard=guard)
            observed = CompanyObservation.model_validate(response.model_dump(mode="python", warnings="error"))
            if (observed.page_id != expected or observed.data_source_id != options.data_source_id
                    or observed.mapping_sha256 != mapping_hash(options) or tuple(value.field for value in observed.values) != fields):
                raise NotionError("NOTION_RESPONSE_INVALID")
        except NotionError as failure: error = failure
        except Exception: error = NotionError("NOTION_RESPONSE_INVALID")
        # Reauthorize even failures: a revoked actor receives no remote data.
        await guard()
        if error:
            code = 409 if error.code in {"NOTION_SCHEMA_CHANGED", "NOTION_PAGE_UNAVAILABLE", "NOTION_OPERATION_BLOCKED"} else 503
            headers = {"Retry-After": str(error.retry_after)} if error.retry_after else None
            raise HTTPException(code, {"code": error.code}, headers=headers)
        values = {item.field: item.value for item in observed.values}
        return {"company_id": str(company_id), "direction": "READ_ONLY", "local_sha256": before["local_sha256"],
            "source": observed.model_dump(mode="json", exclude={"values"}),
            "fields": [{"field": field, "current": before["values"][field], "observed": values[field],
                "changed": before["values"][field] != values[field]} for field in fields]}

    return router
