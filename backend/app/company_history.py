"""Whitelisted company snapshots and atomic append-only change evidence.

Caller authorizes the actor and locks the company before changing it. This module
does not commit, read Notion, invent historical baseline events or apply changes.
"""
from uuid import UUID

from sqlalchemy import func, select

from app.db.models import CompanyChangeEvent
from app.material_review import canonical_hash

FIELDS = ("name", "legal_name", "country", "address", "website", "vat_id", "notion_page_id", "is_active")


def company_snapshot(company):
    return {"id": str(company.id), **{field: getattr(company, field) for field in FIELDS}}


def append_company_change(session, company, actor_id, before, *, action="UPDATED", reason="", source=None,
                          request_key=None, request_hash=None):
    session.flush()
    after = company_snapshot(company)
    if before == after: return None
    if action not in {"CREATED", "UPDATED", "NOTION_ADOPTED"} or not isinstance(actor_id, UUID):
        raise ValueError("Invalid company history context")
    if (action == "CREATED" and before != {}) or (action != "CREATED" and
        (set(before) != set(after) or before["id"] != after["id"])):
        raise ValueError("Invalid company history snapshot")
    if source is None: source = {}
    if action != "NOTION_ADOPTED" and source:
        raise ValueError("Local company changes cannot claim remote provenance")
    version = (session.scalar(select(func.max(CompanyChangeEvent.version)).where(CompanyChangeEvent.company_id == company.id)) or 0) + 1
    event = CompanyChangeEvent(company_id=company.id, actor_id=actor_id, version=version, action=action,
        before_snapshot=before, after_snapshot=after, before_hash=canonical_hash(before), after_hash=canonical_hash(after),
        reason=reason, source=source, request_key=request_key, request_hash=request_hash)
    session.add(event); session.flush()
    return event
