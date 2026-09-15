"""Server-side domain authorization, rechecked inside each database transaction."""
from typing import Annotated
from pathlib import PurePosixPath

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth.dependencies import require_active_user
from app.auth.service import AuthContext, _aware, database_now, lock_user_credential, verify_csrf
from app.db.models import AuthSession, InternalUser, PBRMaterial

ADMIN = frozenset({"ADMIN"})
CATALOG_MANAGERS = frozenset({"ADMIN", "PRODUCTION_LEAD"})
MATERIAL_EDITORS = frozenset({"ADMIN", "PRODUCTION_LEAD", "PROCESSOR"})
APPLICATION_ROLES = frozenset({"ADMIN", "PRODUCTION_LEAD", "PROCESSOR", "LEADERSHIP"})
ACCESS_LOCK = 737824902


class ApplicationAccess:
    def __init__(self, context: AuthContext):
        self.context = context
        self.user = context.user

    def check(self, session: Session, roles=APPLICATION_ROLES, *, exclusive=False) -> InternalUser:
        # Account changes take the exclusive counterpart, so demotion/disable/
        # reset cannot race a domain write authorized from an earlier request.
        # Lock order: access gate, credential, auth session, domain records.
        if session.get_bind().dialect.name == "postgresql":
            function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
            session.execute(text(f"SELECT {function}(:key)"), {"key": ACCESS_LOCK})
        credential = lock_user_credential(session, self.context.user.id)
        stored = session.scalar(select(AuthSession).where(
            AuthSession.id == self.context.session.id,
            AuthSession.user_id == self.context.user.id,
        ).with_for_update().execution_options(populate_existing=True))
        user = session.get(InternalUser, self.context.user.id, populate_existing=True)
        now = database_now(session)
        if (credential is None or user is None or not user.is_active or stored is None
                or stored.revoked_at is not None or now >= _aware(stored.idle_expires_at)
                or now >= _aware(stored.absolute_expires_at)):
            raise HTTPException(401, "Not authenticated.")
        if credential.must_change_password:
            raise HTTPException(403, detail={"code": "PASSWORD_CHANGE_REQUIRED", "message": "Change your password before using the application."})
        if user.role not in roles:
            raise HTTPException(403, "Forbidden.")
        self.user = user
        return user

    def require_material(self, material: PBRMaterial) -> None:
        if self.user.role == "PROCESSOR" and material.assigned_processor_id != self.user.id:
            # Do not disclose the existence of another processor's material.
            raise HTTPException(404, "PBR material not found.")

    def require_folder(self, folder_path: str, technical_identity: str) -> None:
        if self.user.role == "PROCESSOR" and PurePosixPath(folder_path).name != technical_identity:
            raise HTTPException(404, "Material folder not found.")


def require_application_access(
    request: Request, context: AuthContext = Depends(require_active_user),
) -> ApplicationAccess:
    if context.user.credential is None or context.user.credential.must_change_password:
        raise HTTPException(403, detail={"code": "PASSWORD_CHANGE_REQUIRED", "message": "Change your password before using the application."})
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        verify_csrf(request, context, request.app.state.settings)
    return ApplicationAccess(context)


AccessDependency = Annotated[ApplicationAccess, Depends(require_application_access)]
