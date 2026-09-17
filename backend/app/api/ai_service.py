"""Administrator issuance/revocation and exactly two restricted service routes."""
import hashlib
import secrets
from datetime import timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy import and_, func, or_, select

from app.ai_content import AiDraftCreate, publishing_context
from app.ai_draft_saves import receive_ai_draft
from app.ai_service_access import AiServiceDependency
from app.api.material_review import _material, _record, _replay, _request_hash, _state
from app.auth.access import AccessDependency, ADMIN
from app.auth.service import _aware, database_now
from app.catalog import Reason
from app.db.models import AiServiceCredential
from app.material_identity import require_material_idle
from app.schemas import ApiSchema


class ServiceIssue(ApiSchema):
    idempotency_key: UUID
    lifetime_seconds: Annotated[int, Field(strict=True, ge=60, le=3600)] = 900
    reason: Reason


class ServiceRevoke(ApiSchema):
    idempotency_key: UUID
    reason: Reason


def credential_view(item):
    return {"id": str(item.id), "material_id": str(item.material_id), "actor_id": str(item.actor_id),
        "created_at": _aware(item.created_at).isoformat(), "expires_at": _aware(item.expires_at).isoformat(),
        "revoked_at": _aware(item.revoked_at).isoformat() if item.revoked_at else None,
        "scopes": ["publishing-context:read", "content-drafts:write"]}


def build_ai_service_router(database):
    router = APIRouter(prefix="/api", tags=["restricted AI service"])

    @router.post("/materials/{material_id}/ai-service-credentials")
    def issue(material_id: UUID, payload: ServiceIssue, access: AccessDependency):
        request_hash = _request_hash("AI_SERVICE_ISSUED", material_id, payload)
        with database.session() as session:
            actor = access.check(session, ADMIN)
            material = _material(session, material_id, access, lock=True)
            replay = _replay(session, actor.id, material_id, payload, request_hash)
            if replay is not None: return replay
            require_material_idle(session, material_id)
            now = database_now(session)
            active = session.scalar(select(func.count()).select_from(AiServiceCredential).where(
                AiServiceCredential.material_id == material_id, AiServiceCredential.revoked_at.is_(None), AiServiceCredential.expires_at > now))
            if active >= 10: raise HTTPException(409, {"code": "AI_SERVICE_ACTIVE_LIMIT"})
            raw = secrets.token_urlsafe(32)
            item = AiServiceCredential(id=uuid4(), actor_id=actor.id, material_id=material_id,
                issuer_session_id=access.context.session.id, token_hash=hashlib.sha256(raw.encode("ascii")).hexdigest(),
                created_at=now, expires_at=now + timedelta(seconds=payload.lifetime_seconds))
            session.add(item); session.flush()
            safe = {"credential": credential_view(item), "token": None, "secret_available": False}
            # Persist only metadata. The one-time secret is constructed after the
            # transaction commits and never enters an audit or replay record.
            _record(session, material, _state(session, material_id, create=True), actor.id, "AI_SERVICE_ISSUED",
                payload, request_hash, safe, code=201, audit={"credential_id": str(item.id), "expires_at": safe["credential"]["expires_at"], "reason": payload.reason})
            return JSONResponse(status_code=201, content={**safe, "token": f"reawote_ai_{item.id}.{raw}", "secret_available": True})

    @router.post("/materials/{material_id}/ai-service-credentials/{credential_id}/revoke")
    def revoke(material_id: UUID, credential_id: UUID, payload: ServiceRevoke, access: AccessDependency):
        request_hash = _request_hash("AI_SERVICE_REVOKED:" + str(credential_id), material_id, payload)
        with database.session() as session:
            actor = access.check(session, ADMIN)
            # Lock the credential before the material, matching service requests.
            item = session.scalar(select(AiServiceCredential).where(AiServiceCredential.id == credential_id).with_for_update())
            if item is None or item.material_id != material_id: raise HTTPException(404, "AI credential not found.")
            material = _material(session, material_id, access, lock=True)
            replay = _replay(session, actor.id, material_id, payload, request_hash)
            if replay is not None: return replay
            if item.revoked_at is None: item.revoked_at = database_now(session)
            body = credential_view(item)
            return _record(session, material, _state(session, material_id, create=True), actor.id, "AI_SERVICE_REVOKED",
                payload, request_hash, body, audit={"credential_id": str(item.id), "reason": payload.reason})

    @router.get("/materials/{material_id}/ai-service-credentials")
    def history(material_id: UUID, access: AccessDependency, after: UUID | None = None,
                limit: Annotated[int, Query(ge=1, le=50)] = 20):
        with database.session() as session:
            access.check(session, ADMIN); _material(session, material_id, access)
            query = select(AiServiceCredential).where(AiServiceCredential.material_id == material_id)
            if after:
                cursor = session.get(AiServiceCredential, after)
                if cursor is None or cursor.material_id != material_id: raise HTTPException(404, "AI credential cursor not found.")
                stamp = select(AiServiceCredential.created_at).where(AiServiceCredential.id == after).scalar_subquery()
                query = query.where(or_(AiServiceCredential.created_at < stamp, and_(AiServiceCredential.created_at == stamp, AiServiceCredential.id < after)))
            rows = list(session.scalars(query.order_by(AiServiceCredential.created_at.desc(), AiServiceCredential.id.desc()).limit(limit + 1)))
            return {"items": [credential_view(item) for item in rows[:limit]], "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None}

    @router.get("/ai/materials/{material_id}/publishing-context")
    def context(material_id: UUID, access: AiServiceDependency):
        with database.session() as session:
            access.check(session)
            material = _material(session, material_id, access, lock=True, mutating=True)
            body = publishing_context(session, material)
            access.check(session)  # Use wall time after every possible domain wait.
            return body

    @router.post("/ai/materials/{material_id}/content-drafts")
    def draft(material_id: UUID, payload: AiDraftCreate, access: AiServiceDependency):
        return receive_ai_draft(database, material_id, payload, access, service=True)

    return router
