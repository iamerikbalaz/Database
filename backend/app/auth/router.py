from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select

from app.auth.dependencies import require_active_user
from app.auth.schemas import (
    AuthSessionResponse,
    ChangePasswordRequest,
    ChangePasswordResponse,
    LoginRequest,
    LogoutResponse,
    PublicUser,
)
from app.auth.security import PasswordPolicyError, PasswordService, validate_new_password
from app.auth.service import (
    GENERIC_LOGIN_ERROR,
    AuthContext,
    SessionDatabase,
    _aware,
    audit,
    change_credential_password,
    check_login_rate_limit,
    clear_login_rate_limit,
    create_session,
    database_now,
    delete_session_cookie,
    find_login_user,
    lock_user_credential,
    request_client_identifier,
    revoke_all_user_sessions,
    set_session_cookie,
    verify_csrf,
    verify_request_source,
)
from app.core.config import Settings
from app.db.models import AuthSession


def _response(context: AuthContext, csrf_token: str | None = None) -> AuthSessionResponse:
    credential = context.user.credential
    if credential is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")
    return AuthSessionResponse(
        user=PublicUser.model_validate(context.user),
        must_change_password=credential.must_change_password,
        csrf_token=csrf_token or context.session.csrf_token,
    )


def build_auth_router(database: SessionDatabase, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])
    password_service = PasswordService(settings)

    @router.post("/login", response_model=AuthSessionResponse)
    def login(payload: LoginRequest, request: Request, response: Response) -> AuthSessionResponse:
        verify_request_source(request, settings)
        with database.session() as db_session:
            key_hash, bucket = check_login_rate_limit(
                db_session,
                settings,
                email=payload.email,
                client_identifier=request_client_identifier(request),
                now=database_now(db_session),
            )
            user = find_login_user(db_session, payload.email)
            credential = lock_user_credential(db_session, user.id) if user is not None else None
            if credential is None:
                password_service.dummy_verify(payload.password)
                verified = False
            else:
                verified = password_service.verify_password(
                    credential.password_hash, payload.password
                )

            if user is None or credential is None or not user.is_active or not verified:
                if user is not None and not user.is_active:
                    audit("inactive_account", user_id=user.id)
                audit("login_failure", reason="invalid_credentials")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=GENERIC_LOGIN_ERROR,
                )

            clear_login_rate_limit(db_session, key_hash, bucket)
            auth_session, raw_token, csrf_token = create_session(
                db_session, settings, user, now=database_now(db_session)
            )
            result = _response(AuthContext(user=user, session=auth_session), csrf_token)
            # The credentials lock covers verification, session insertion and commit.
            db_session.commit()
            set_session_cookie(response, settings, raw_token)
            audit("login_success", user_id=user.id)
            return result

    @router.get("/session", response_model=AuthSessionResponse)
    def current_session(
        context: AuthContext = Depends(require_active_user),
    ) -> AuthSessionResponse:
        return _response(context)

    @router.post("/logout", response_model=LogoutResponse)
    def logout(
        request: Request,
        response: Response,
        context: AuthContext = Depends(require_active_user),
    ) -> LogoutResponse:
        verify_csrf(request, context, settings)
        with database.session() as db_session:
            stored = db_session.scalar(
                select(AuthSession).where(AuthSession.id == context.session.id).with_for_update()
            )
            if stored is None or stored.revoked_at is not None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated."
                )
            stored.revoked_at = max(database_now(db_session), _aware(stored.created_at))
            db_session.commit()
        delete_session_cookie(response, settings)
        audit("logout", user_id=context.user.id)
        audit("session_revoked", user_id=context.user.id, reason="logout")
        return LogoutResponse()

    @router.post("/change-password", response_model=ChangePasswordResponse)
    def change_password(
        payload: ChangePasswordRequest,
        request: Request,
        response: Response,
        context: AuthContext = Depends(require_active_user),
    ) -> ChangePasswordResponse:
        verify_csrf(request, context, settings)
        with database.session() as db_session:
            credential = lock_user_credential(db_session, context.user.id)
            # Authentication ran before this transaction. Recheck its session
            # after the credentials lock, before verifying any password. A
            # preceding change may revoke it while this request waits; revealing
            # password correctness to that revoked session would create an oracle.
            stored = db_session.scalar(
                select(AuthSession)
                .where(AuthSession.id == context.session.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            now = database_now(db_session)
            if (
                stored is None
                or stored.revoked_at is not None
                or now >= _aware(stored.idle_expires_at)
                or now >= _aware(stored.absolute_expires_at)
                or credential is None
            ):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated."
                )
            if not password_service.verify_password(
                credential.password_hash, payload.current_password
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Current password is incorrect.",
                )
            try:
                normalized = validate_new_password(
                    payload.new_password,
                    email=context.user.email,
                    display_name=context.user.display_name,
                )
            except PasswordPolicyError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=str(exc),
                ) from exc
            if password_service.verify_password(credential.password_hash, normalized):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="New password must differ from the current password.",
                )
            now = max(database_now(db_session), _aware(credential.password_changed_at))
            change_credential_password(credential, password_service, normalized, now)
            revoke_all_user_sessions(db_session, context.user.id, now)
            db_session.commit()
        delete_session_cookie(response, settings)
        audit("password_change", user_id=context.user.id)
        audit("session_revoked", user_id=context.user.id, reason="password_change")
        return ChangePasswordResponse(changed_at=now)

    return router
