"""Human-authenticated context/source approval and external AI proposal intake.

This router does not grant access to external services. Separate scoped service
credentials are a subsequent delivery step. Human adoption uses the ordinary
content revision and approval workflow.
"""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.ai_content import (AiDraftCreate, AiDraftAdopt, SourceLinkActivity, SourceLinkCreate,
    ai_draft_view, publishing_context, source_link_view)
from app.api.material_review import _material, _record, _replay, _request_hash, _state
from app.auth.access import ADMIN, AccessDependency
from app.db.models import MaterialAiDraft, MaterialSourceLink
from app.material_identity import require_material_idle
from app.material_review import invalidate_review
from app.content_saves import save_material_content
from app.ai_draft_saves import receive_ai_draft


def build_ai_content_router(database):
    router = APIRouter(prefix="/api/materials", tags=["AI content provenance"])

    @router.get("/{material_id}/publishing-context")
    def context(material_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session)
            material = _material(session, material_id, access, lock=True, mutating=True)
            return publishing_context(session, material)

    @router.get("/{material_id}/content-sources")
    def sources(material_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session); _material(session, material_id, access)
            # Bounded permanent references, including retired ones, make this a
            # complete small registry rather than an implicitly truncated history.
            return [source_link_view(item) for item in session.scalars(select(MaterialSourceLink)
                .where(MaterialSourceLink.material_id == material_id).order_by(MaterialSourceLink.created_at, MaterialSourceLink.id))]

    def change_source(material_id, access, payload, source_id=None):
        request_hash = _request_hash("CONTENT_SOURCE_" + (str(source_id) if source_id else "CREATE"), material_id, payload)
        with database.session() as session:
            actor = access.check(session, ADMIN)
            material = _material(session, material_id, access, lock=True)
            replay = _replay(session, actor.id, material_id, payload, request_hash)
            if replay is not None: return replay
            require_material_idle(session, material_id)
            state = _state(session, material_id, create=True)
            if source_id is None:
                total = session.scalar(select(func.count()).select_from(MaterialSourceLink).where(MaterialSourceLink.material_id == material_id))
                if total >= 100: raise HTTPException(409, {"code": "CONTENT_SOURCE_HISTORY_LIMIT"})
                item = MaterialSourceLink(material_id=material_id, actor_id=actor.id, url=payload.url)
                session.add(item)
                changed = True
            else:
                item = session.get(MaterialSourceLink, source_id)
                if item is None or item.material_id != material_id: raise HTTPException(404, "Source reference not found.")
                if item.version != payload.expected_version: raise HTTPException(409, {"code": "CONTENT_SOURCE_VERSION_CHANGED"})
                changed = item.is_active != payload.is_active
                if changed: item.is_active = payload.is_active; item.version += 1
            try: session.flush()
            except IntegrityError:
                session.rollback(); raise HTTPException(409, {"code": "CONTENT_SOURCE_EXISTS"}) from None
            active = session.scalar(select(func.count()).select_from(MaterialSourceLink).where(MaterialSourceLink.material_id == material_id, MaterialSourceLink.is_active.is_(True)))
            if active > 20: raise HTTPException(409, {"code": "CONTENT_SOURCE_ACTIVE_LIMIT"})
            if changed: invalidate_review(session, material, actor.id, "CONTENT_SOURCES_CHANGED", record_event=False)
            body = source_link_view(item)
            return _record(session, material, state, actor.id, "CONTENT_SOURCE_APPROVED" if source_id is None else "CONTENT_SOURCE_ACTIVITY",
                payload, request_hash, body, code=201 if source_id is None else 200, audit={"source_id": str(item.id), "version": item.version, "is_active": item.is_active, "reason": payload.reason})

    @router.post("/{material_id}/content-sources")
    def approve_source(material_id: UUID, payload: SourceLinkCreate, access: AccessDependency):
        return change_source(material_id, access, payload)

    @router.patch("/{material_id}/content-sources/{source_id}")
    def source_activity(material_id: UUID, source_id: UUID, payload: SourceLinkActivity, access: AccessDependency):
        return change_source(material_id, access, payload, source_id)

    @router.post("/{material_id}/content-drafts")
    def draft(material_id: UUID, payload: AiDraftCreate, access: AccessDependency):
        return receive_ai_draft(database, material_id, payload, access)

    @router.post("/{material_id}/content-drafts/{draft_id}/adopt")
    def adopt(material_id: UUID, draft_id: UUID, payload: AiDraftAdopt, access: AccessDependency):
        return save_material_content(database, material_id, payload, access, source_draft_id=draft_id)

    @router.get("/{material_id}/content-drafts")
    def drafts(material_id: UUID, access: AccessDependency, limit: Annotated[int, Query(ge=1, le=50)] = 20, after: UUID | None = None):
        with database.session() as session:
            access.check(session); material = _material(session, material_id, access, lock=True)
            current = publishing_context(session, material)
            query = select(MaterialAiDraft).where(MaterialAiDraft.material_id == material_id)
            if after is not None:
                cursor = session.get(MaterialAiDraft, after)
                if cursor is None or cursor.material_id != material_id: raise HTTPException(404, "Draft cursor not found.")
                # Keep the native timestamp representation for SQLite and PG.
                stamp = select(MaterialAiDraft.created_at).where(MaterialAiDraft.id == after).scalar_subquery()
                query = query.where(or_(MaterialAiDraft.created_at < stamp, and_(MaterialAiDraft.created_at == stamp, MaterialAiDraft.id < after)))
            items = list(session.scalars(query.order_by(MaterialAiDraft.created_at.desc(), MaterialAiDraft.id.desc()).limit(limit + 1)))
            return {"items": [{**ai_draft_view(item), "context_is_current": item.context_hash == current["context_hash"]} for item in items[:limit]],
                "next_cursor": str(items[limit - 1].id) if len(items) > limit else None}

    return router
