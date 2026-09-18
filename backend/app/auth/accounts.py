"""Credential provisioning and reset for an existing active profile."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session

from app.auth.access import ADMIN, AccessDependency
from app.auth.schemas import ChangePasswordRequest
from app.auth.security import PasswordPolicyError, PasswordService, validate_new_password
from app.auth.service import (
    _aware, audit, database_now, lock_user_credential, revoke_all_user_sessions,
)
from app.core.config import Settings
from app.db.models import InternalUser, UserCredential
from app.schemas import ApiSchema
from app.account_security_history import append_security_event


class AccessResetResponse(ApiSchema):
    user_id: UUID
    must_change_password: bool = True
    changed_at: datetime


def set_initial_access(session: Session, user: InternalUser, service: PasswordService,
                       password: str, *, source: str, actor_id: UUID | None) -> datetime:
    if source not in {"ADMIN", "HOST"}: raise ValueError("Invalid access source")
    if not user.is_active:
        raise HTTPException(409, "Activate the account before provisioning access.")
    try:
        normalized = validate_new_password(password, email=user.email, display_name=user.display_name)
    except PasswordPolicyError as exc:
        raise HTTPException(422, str(exc)) from None
    credential = lock_user_credential(session, user.id)
    was_provisioned = credential is not None
    now = database_now(session)
    if credential is None:
        credential = UserCredential(user_id=user.id)
        session.add(credential)
    else:
        now = max(now, _aware(credential.password_changed_at))
        if service.verify_password(credential.password_hash, normalized):
            raise HTTPException(422, "New password must differ from the current password.")
    credential.password_hash = service.hash_password(normalized)
    credential.must_change_password = True
    credential.password_changed_at = now
    credential.updated_at = now
    revoke_all_user_sessions(session, user.id, now)
    action = "HOST_ACCESS_RECOVERED" if source == "HOST" else "ADMIN_ACCESS_RESET" if was_provisioned else "ADMIN_ACCESS_PROVISIONED"
    append_security_event(session, user.id, action, actor_id)
    return now


def build_account_router(database, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/auth/accounts", tags=["accounts"])
    service = PasswordService(settings)

    @router.post("/{user_id}/access", response_model=AccessResetResponse)
    def reset_access(user_id: UUID, payload: ChangePasswordRequest,
                     access: AccessDependency) -> AccessResetResponse:
        with database.session() as session:
            actor = access.check(session, ADMIN, exclusive=True)
            credential = lock_user_credential(session, actor.id)
            if not service.verify_password(credential.password_hash, payload.current_password):
                raise HTTPException(400, "Current password is incorrect.")
            if actor.id == user_id:
                raise HTTPException(409, "Use change-password for your own account.")
            user = session.get(InternalUser, user_id)
            if user is None:
                raise HTTPException(404, "Internal user not found.")
            changed_at = set_initial_access(session, user, service, payload.new_password, source="ADMIN", actor_id=actor.id)
            session.commit()
        audit("access_reset", user_id=user_id)
        return AccessResetResponse(user_id=user_id, changed_at=changed_at)

    return router
