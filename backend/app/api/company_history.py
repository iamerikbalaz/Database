"""Bounded administrator history of company changes made through the application."""
from datetime import UTC
from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.auth.access import ADMIN, AccessDependency
from app.company_history import FIELDS
from app.db.models import Company, CompanyChangeEvent
from app.material_review import canonical_hash


def event_view(event):
    expected = set(FIELDS) | {"id"}
    if (not isinstance(event.before_snapshot, dict) or not isinstance(event.after_snapshot, dict)
            or set(event.after_snapshot) != expected or event.after_snapshot["id"] != str(event.company_id)
            or (event.action != "CREATED" and set(event.before_snapshot) != expected)
            or (event.action == "CREATED" and event.before_snapshot != {})
            or canonical_hash(event.before_snapshot) != event.before_hash
            or canonical_hash(event.after_snapshot) != event.after_hash):
        raise HTTPException(503, {"code": "COMPANY_HISTORY_INVALID"})
    timestamp = event.created_at if event.created_at.tzinfo else event.created_at.replace(tzinfo=UTC)
    return {"id": str(event.id), "company_id": str(event.company_id), "actor_id": str(event.actor_id),
        "version": event.version, "action": event.action, "before": event.before_snapshot, "after": event.after_snapshot,
        "before_sha256": event.before_hash, "after_sha256": event.after_hash, "reason": event.reason,
        "source": event.source, "created_at": timestamp.isoformat()}


def build_company_history_router(database):
    router = APIRouter(tags=["company history"])

    @router.get("/api/companies/{company_id}/history")
    def history(company_id: UUID, access: AccessDependency, after: UUID | None = None):
        with database.session() as session:
            access.check(session, ADMIN)
            if session.get(Company, company_id) is None: raise HTTPException(404, "Company not found.")
            statement = select(CompanyChangeEvent).where(CompanyChangeEvent.company_id == company_id)
            if after is not None:
                cursor = session.scalar(select(CompanyChangeEvent).where(CompanyChangeEvent.id == after, CompanyChangeEvent.company_id == company_id))
                if cursor is None: raise HTTPException(409, {"code": "COMPANY_HISTORY_CURSOR_INVALID"})
                statement = statement.where(CompanyChangeEvent.version < cursor.version)
            records = list(session.scalars(statement.order_by(CompanyChangeEvent.version.desc()).limit(21)))
            return {"company_id": str(company_id), "items": [event_view(item) for item in records[:20]],
                "next_cursor": str(records[19].id) if len(records) > 20 else None}

    return router
