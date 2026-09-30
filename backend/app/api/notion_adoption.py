"""Legacy receipt reads survive; the application never adopts inbound Notion edits."""
from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.company_history import event_view
from app.auth.access import ADMIN, AccessDependency
from app.db.models import CompanyChangeEvent


def build_notion_adoption_router(database, reader, settings):
    router = APIRouter(tags=["Historical Notion adoption"])

    @router.get("/api/companies/{company_id}/notion-adoptions/{request_key}")
    def recovery(company_id: UUID, request_key: UUID, access: AccessDependency):
        with database.session() as session:
            actor = access.check(session, ADMIN)
            event = session.scalar(select(CompanyChangeEvent).where(
                CompanyChangeEvent.actor_id == actor.id,
                CompanyChangeEvent.company_id == company_id,
                CompanyChangeEvent.request_key == request_key,
                CompanyChangeEvent.action == "NOTION_ADOPTED"))
            if event is None:
                raise HTTPException(404, {"code": "NOTION_ADOPTION_NOT_FOUND"})
            return {"request_key": str(event.request_key), "event": event_view(event)}

    @router.post("/api/companies/{company_id}/notion-adopt")
    def retired_adoption(company_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session, ADMIN)
            raise HTTPException(410, {"code": "NOTION_INBOUND_DISABLED", "message": "Notion changes are not imported. Edit Customers or Orders in the application."})

    return router
