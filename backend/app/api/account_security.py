"""Administrator-only immutable account security outcome history."""
from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.auth.access import ADMIN, AccessDependency
from app.auth.service import _aware
from app.db.models import AccountSecurityEvent, InternalUser


def build_account_security_history_router(database):
    router = APIRouter(prefix="/api/internal-users", tags=["account security history"])

    @router.get("/{user_id}/security-history")
    def history(user_id: UUID, access: AccessDependency, after: UUID | None = None):
        with database.session() as session:
            access.check(session, ADMIN)
            if session.get(InternalUser, user_id) is None: raise HTTPException(404, "Internal user not found.")
            query = select(AccountSecurityEvent).where(AccountSecurityEvent.user_id == user_id)
            if after is not None:
                cursor = session.scalar(select(AccountSecurityEvent).where(AccountSecurityEvent.id == after, AccountSecurityEvent.user_id == user_id))
                if cursor is None: raise HTTPException(409, {"code": "ACCOUNT_SECURITY_CURSOR_INVALID"})
                query = query.where(AccountSecurityEvent.version < cursor.version)
            items = list(session.scalars(query.order_by(AccountSecurityEvent.version.desc()).limit(21)))
            return {"user_id": str(user_id), "items": [dict(id=str(item.id), user_id=str(item.user_id),
                actor_id=str(item.actor_id) if item.actor_id else None, version=item.version, action=item.action,
                requires_password_change=item.requires_password_change,
                credential_changed_at=_aware(item.credential_changed_at).isoformat(), created_at=_aware(item.created_at).isoformat()) for item in items[:20]],
                "next_cursor": str(items[19].id) if len(items) > 20 else None}

    return router
