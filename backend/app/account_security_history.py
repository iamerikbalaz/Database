"""Successful security outcomes only; never accept a password or request body."""
from uuid import UUID

from sqlalchemy import func, select

from app.auth.service import _aware, database_now, lock_user_credential
from app.db.models import AccountSecurityEvent, InternalUser

ACTIONS = frozenset({"FIRST_ADMIN_PROVISIONED", "HOST_ACCESS_RECOVERED", "ADMIN_ACCESS_PROVISIONED", "ADMIN_ACCESS_RESET", "SELF_PASSWORD_CHANGED"})


def append_security_event(session, user_id, action, actor_id=None):
    if action not in ACTIONS or not isinstance(user_id, UUID):
        raise ValueError("Invalid account security history context")
    if action in {"FIRST_ADMIN_PROVISIONED", "HOST_ACCESS_RECOVERED"}:
        valid_actor = actor_id is None
    else:
        valid_actor = isinstance(actor_id, UUID) and ((actor_id == user_id) == (action == "SELF_PASSWORD_CHANGED"))
    if not valid_actor: raise ValueError("Invalid account security history actor")
    session.flush()
    credential = lock_user_credential(session, user_id)
    user = session.scalar(select(InternalUser).where(InternalUser.id == user_id).with_for_update())
    if user is None or credential is None or credential.must_change_password != (action != "SELF_PASSWORD_CHANGED"):
        raise ValueError("Invalid account security history outcome")
    version = (session.scalar(select(func.max(AccountSecurityEvent.version)).where(AccountSecurityEvent.user_id == user_id)) or 0) + 1
    if action == "FIRST_ADMIN_PROVISIONED" and version != 1:
        raise ValueError("Bootstrap must start account security history")
    item = AccountSecurityEvent(user_id=user_id, actor_id=actor_id, action=action, version=version,
        requires_password_change=credential.must_change_password, credential_changed_at=_aware(credential.password_changed_at),
        created_at=database_now(session))
    session.add(item); session.flush()
    return item
