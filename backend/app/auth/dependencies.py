from collections.abc import Callable

from fastapi import Depends, HTTPException, status

from app.auth.service import AuthContext, load_auth_context
from app.db.models import InternalUserRole


def get_current_auth_context(context: AuthContext = Depends(load_auth_context)) -> AuthContext:
    return context


def require_authenticated_user(
    context: AuthContext = Depends(get_current_auth_context),
) -> AuthContext:
    return context


def require_active_user(
    context: AuthContext = Depends(require_authenticated_user),
) -> AuthContext:
    if not context.user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")
    return context


def require_roles(*roles: InternalUserRole) -> Callable[..., AuthContext]:
    allowed = {role.value for role in roles}

    def dependency(context: AuthContext = Depends(require_active_user)) -> AuthContext:
        if context.user.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")
        return context

    return dependency
