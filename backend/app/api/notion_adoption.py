"""Explicit local adoption of reviewed Notion fields; never writes to Notion."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.api.company_history import event_view
from app.auth.access import ADMIN, AccessDependency
from app.company_history import append_company_change, company_snapshot
from app.db.models import Company, CompanyChangeEvent
from app.material_review import canonical_hash
from app.notion_reader import CompanyObservation, NotionError, configuration_from_settings, mapping_hash, page_id
from app.schemas import ApiSchema, Sha256

class NotionAdopt(ApiSchema):
    request_key: UUID
    expected_page_id: str = Field(min_length=32, max_length=36, strict=True)
    expected_local_sha256: Sha256
    expected_observation_sha256: Sha256
    selected_fields: list[Literal["name", "legal_name", "country", "address", "website", "vat_id"]] = Field(min_length=1, max_length=6)
    reason: str = Field(min_length=1, max_length=2000, strict=True)

    @field_validator("request_key")
    @classmethod
    def nonzero_key(cls, value):
        if value.int == 0: raise ValueError("A nonzero request key is required")
        return value

    @field_validator("expected_page_id")
    @classmethod
    def linked_page(cls, value):
        try: return page_id(value)
        except NotionError: raise ValueError("An explicit page ID is required") from None

    @field_validator("selected_fields")
    @classmethod
    def unique_fields(cls, value):
        if len(set(value)) != len(value): raise ValueError("Select each field once")
        return sorted(value)

    @field_validator("reason")
    @classmethod
    def explanation(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 and char not in "\n\r\t" or ord(char) == 127 for char in value):
            raise ValueError("A readable reason is required")
        return value


def _company(session, identifier, *, lock=False):
    query = select(Company).where(Company.id == identifier).execution_options(populate_existing=True)
    company = session.scalar(query.with_for_update() if lock else query)
    if company is None: raise HTTPException(404, "Company not found.")
    return company


def _check_local(company, payload):
    try: linked = page_id(company.notion_page_id)
    except NotionError: raise HTTPException(409, {"code": "NOTION_LINK_REQUIRED"}) from None
    if linked != payload.expected_page_id: raise HTTPException(409, {"code": "NOTION_LINK_CHANGED"})
    # Same binding as the comparison route, including the last local write time.
    snapshot = company_snapshot(company) | {"updated_at": company.updated_at.isoformat()}
    if canonical_hash(snapshot) != payload.expected_local_sha256:
        raise HTTPException(409, {"code": "NOTION_LOCAL_CHANGED"})


def _result(event):
    return {"request_key": str(event.request_key), "event": event_view(event)}


def _replay(session, actor_id, identifier, payload, digest):
    event = session.scalar(select(CompanyChangeEvent).where(
        CompanyChangeEvent.actor_id == actor_id, CompanyChangeEvent.request_key == payload.request_key))
    if event is None: return None
    if event.company_id != identifier or event.action != "NOTION_ADOPTED" or event.request_hash != digest:
        raise HTTPException(409, {"code": "NOTION_REQUEST_KEY_REUSED"})
    return _result(event)


class _AlreadyCommitted(Exception):
    """Stop remote IO; finalize reauthorizes and returns immutable saved evidence."""


def build_notion_adoption_router(database, reader, settings):
    router = APIRouter(tags=["Reviewed Notion adoption"])

    @router.get("/api/companies/{company_id}/notion-adoptions/{request_key}")
    def recovery(company_id: UUID, request_key: UUID, access: AccessDependency):
        with database.session() as session:
            actor = access.check(session, ADMIN)
            event = session.scalar(select(CompanyChangeEvent).where(CompanyChangeEvent.actor_id == actor.id,
                CompanyChangeEvent.company_id == company_id, CompanyChangeEvent.request_key == request_key,
                CompanyChangeEvent.action == "NOTION_ADOPTED"))
            if event is None: raise HTTPException(404, {"code": "NOTION_ADOPTION_NOT_FOUND"})
            return _result(event)

    @router.post("/api/companies/{company_id}/notion-adopt")
    async def adopt(company_id: UUID, payload: NotionAdopt, access: AccessDependency):
        digest = canonical_hash({"operation": "NOTION_ADOPT", "company_id": str(company_id),
            "request": payload.model_dump(mode="json")})

        def check():
            with database.session() as session:
                actor = access.check(session, ADMIN)
                replay = _replay(session, actor.id, company_id, payload, digest)
                if replay is not None: return replay
                _check_local(_company(session, company_id), payload)
                return None

        replay = await run_in_threadpool(check)
        if replay is not None: return replay

        async def guard():
            if await run_in_threadpool(check) is not None: raise _AlreadyCommitted()

        observed = None; error = None
        try:
            options = configuration_from_settings(settings)
            if not options.enabled: raise HTTPException(503, {"code": "NOTION_DISABLED"})
            if reader.configuration != options: raise HTTPException(503, {"code": "NOTION_CONFIGURATION_INVALID"})
            fields = tuple(sorted(item.field for item in options.properties))
            if not set(payload.selected_fields) <= set(fields):
                raise HTTPException(409, {"code": "NOTION_SELECTION_INVALID"})
            response = await reader.company(payload.expected_page_id, operation_guard=guard)
            observed = CompanyObservation.model_validate(response.model_dump(mode="python", warnings="error"))
            if (observed.page_id != payload.expected_page_id or observed.data_source_id != options.data_source_id
                    or observed.mapping_sha256 != mapping_hash(options) or tuple(item.field for item in observed.values) != fields):
                raise NotionError("NOTION_RESPONSE_INVALID")
        except HTTPException as failure: error = failure
        except NotionError as failure:
            status = 409 if failure.code in {"NOTION_SCHEMA_CHANGED", "NOTION_PAGE_UNAVAILABLE", "NOTION_OPERATION_BLOCKED"} else 503
            headers = {"Retry-After": str(failure.retry_after)} if failure.retry_after else None
            error = HTTPException(status, {"code": failure.code}, headers=headers)
        except Exception: error = HTTPException(503, {"code": "NOTION_RESPONSE_INVALID"})

        def finish():
            with database.session() as session:
                actor = access.check(session, ADMIN)
                # Another request may have committed during IO, including a failed
                # read. Authorization and exact replay always precede stale checks.
                replay = _replay(session, actor.id, company_id, payload, digest)
                if replay is not None: return replay
                company = _company(session, company_id, lock=True)
                _check_local(company, payload)
                if error is not None: raise error
                if observed.observation_sha256 != payload.expected_observation_sha256:
                    raise HTTPException(409, {"code": "NOTION_OBSERVATION_CHANGED"})
                values = {item.field: item.value for item in observed.values}
                before = company_snapshot(company)
                if any(before[field] == values[field] for field in payload.selected_fields):
                    raise HTTPException(409, {"code": "NOTION_SELECTION_UNCHANGED"})
                for field in payload.selected_fields: setattr(company, field, values[field])
                source = observed.model_dump(mode="json", exclude={"values"}) | {"selected_fields": payload.selected_fields}
                try:
                    event = append_company_change(session, company, actor.id, before, action="NOTION_ADOPTED",
                        reason=payload.reason, source=source, request_key=payload.request_key, request_hash=digest)
                    result = _result(event)
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    raise HTTPException(409, {"code": "NOTION_ADOPTION_CONFLICT"}) from None
                return result

        return await run_in_threadpool(finish)

    return router
