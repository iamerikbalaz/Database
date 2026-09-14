from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from fastapi import HTTPException, Request, Response, status
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, joinedload

from app.auth.security import (
    PasswordService,
    generate_secret_token,
    rate_limit_key,
    token_digest,
)
from app.core.config import Settings
from app.db.models import AuthLoginRateLimit, AuthSession, InternalUser, UserCredential


logger = logging.getLogger("reawote.auth")
GENERIC_LOGIN_ERROR = "Invalid email or password."


class SessionDatabase(Protocol):
    def session(self): ...


@dataclass(frozen=True)
class AuthContext:
    user: InternalUser
    session: AuthSession


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def audit(event: str, *, user_id: object | None = None, reason: str | None = None) -> None:
    fields: dict[str, object] = {"auth_event": event}
    if user_id is not None:
        fields["user_id"] = str(user_id)
    if reason is not None:
        fields["reason"] = reason
    logger.info("auth_event=%s", event, extra=fields)


def request_client_identifier(request: Request) -> str:
    if request.client is None or not request.client.host:
        return "unknown"
    return request.client.host


def check_login_rate_limit(
    db_session: Session,
    settings: Settings,
    *,
    email: str,
    client_identifier: str,
    now: datetime,
) -> tuple[str, int]:
    key_hash = rate_limit_key(email, client_identifier)
    bucket = int(now.timestamp()) // settings.auth_rate_limit_window_seconds
    values = {"key_hash": key_hash, "window_bucket": bucket, "attempt_count": 1}
    db_session.execute(
        delete(AuthLoginRateLimit).where(AuthLoginRateLimit.window_bucket < bucket - 1)
    )
    dialect = db_session.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(AuthLoginRateLimit).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["key_hash", "window_bucket"],
            set_={"attempt_count": AuthLoginRateLimit.attempt_count + 1},
        ).returning(AuthLoginRateLimit.attempt_count)
        count = db_session.scalar(statement)
    elif dialect == "sqlite":
        statement = sqlite_insert(AuthLoginRateLimit).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["key_hash", "window_bucket"],
            set_={"attempt_count": AuthLoginRateLimit.attempt_count + 1},
        ).returning(AuthLoginRateLimit.attempt_count)
        count = db_session.scalar(statement)
    else:
        item = db_session.get(AuthLoginRateLimit, (key_hash, bucket))
        if item is None:
            item = AuthLoginRateLimit(**values)
            db_session.add(item)
        else:
            item.attempt_count += 1
        db_session.flush()
        count = item.attempt_count
    db_session.commit()
    if count is None or count > settings.auth_rate_limit_attempts:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again later.",
            headers={"Retry-After": str(settings.auth_rate_limit_window_seconds)},
        )
    return key_hash, bucket


def clear_login_rate_limit(db_session: Session, key_hash: str, bucket: int) -> None:
    db_session.execute(
        delete(AuthLoginRateLimit).where(
            AuthLoginRateLimit.key_hash == key_hash,
            AuthLoginRateLimit.window_bucket == bucket,
        )
    )
    db_session.commit()


def create_session(
    db_session: Session,
    settings: Settings,
    user: InternalUser,
    *,
    now: datetime,
) -> tuple[AuthSession, str, str]:
    raw_session_token = generate_secret_token()
    raw_csrf_token = generate_secret_token()
    absolute_expires_at = now + timedelta(hours=settings.auth_absolute_timeout_hours)
    idle_expires_at = min(
        now + timedelta(minutes=settings.auth_idle_timeout_minutes),
        absolute_expires_at,
    )
    auth_session = AuthSession(
        user_id=user.id,
        token_hash=token_digest(raw_session_token),
        csrf_token=raw_csrf_token,
        created_at=now,
        last_seen_at=now,
        idle_expires_at=idle_expires_at,
        absolute_expires_at=absolute_expires_at,
    )
    db_session.add(auth_session)
    db_session.commit()
    db_session.refresh(auth_session)
    return auth_session, raw_session_token, raw_csrf_token


def set_session_cookie(response: Response, settings: Settings, raw_token: str) -> None:
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=raw_token,
        secure=settings.effective_auth_cookie_secure,
        httponly=True,
        samesite="strict",
        path="/",
    )


def delete_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.auth_cookie_name,
        secure=settings.effective_auth_cookie_secure,
        httponly=True,
        samesite="strict",
        path="/",
    )


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")


def load_auth_context(request: Request) -> AuthContext:
    database: SessionDatabase = request.app.state.database
    settings: Settings = request.app.state.settings
    raw_token = request.cookies.get(settings.auth_cookie_name)
    if not raw_token:
        raise _unauthorized()
    try:
        digest = token_digest(raw_token)
    except (UnicodeEncodeError, ValueError):
        raise _unauthorized() from None

    now = datetime.now(UTC)
    with database.session() as db_session:
        auth_session = db_session.scalar(
            select(AuthSession)
            .options(joinedload(AuthSession.user).joinedload(InternalUser.credential))
            .where(AuthSession.token_hash == digest)
        )
        if auth_session is None or auth_session.revoked_at is not None:
            raise _unauthorized()
        if now >= _aware(auth_session.absolute_expires_at) or now >= _aware(
            auth_session.idle_expires_at
        ):
            auth_session.revoked_at = now
            db_session.commit()
            audit("session_expired", user_id=auth_session.user_id)
            raise _unauthorized()
        if not auth_session.user.is_active:
            auth_session.revoked_at = now
            db_session.commit()
            audit("inactive_account", user_id=auth_session.user_id)
            raise _unauthorized()
        if now - _aware(auth_session.last_seen_at) >= timedelta(
            seconds=settings.auth_last_seen_update_seconds
        ):
            auth_session.last_seen_at = now
            auth_session.idle_expires_at = min(
                now + timedelta(minutes=settings.auth_idle_timeout_minutes),
                _aware(auth_session.absolute_expires_at),
            )
            db_session.commit()
        return AuthContext(user=auth_session.user, session=auth_session)


def verify_request_source(request: Request, settings: Settings) -> None:
    origin = request.headers.get("origin")
    if origin is not None:
        if origin.rstrip("/") not in {item.rstrip("/") for item in settings.parsed_cors_origins}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Untrusted request origin.",
            )
        return

    referer = request.headers.get("referer")
    if referer is not None:
        if not any(
            referer == origin_value or referer.startswith(origin_value.rstrip("/") + "/")
            for origin_value in settings.parsed_cors_origins
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Untrusted request origin.",
            )
        return

    if request.headers.get("sec-fetch-site") not in {"same-origin", "same-site"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing request origin proof.",
        )


def verify_csrf(request: Request, context: AuthContext, settings: Settings) -> None:
    verify_request_source(request, settings)
    supplied = request.headers.get("x-csrf-token")
    if not supplied:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token.")
    try:
        supplied_digest = token_digest(supplied)
    except (UnicodeEncodeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token."
        ) from None
    if not secrets.compare_digest(supplied_digest, token_digest(context.session.csrf_token)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token.")


def find_login_user(db_session: Session, email: str) -> InternalUser | None:
    return db_session.scalar(
        select(InternalUser)
        .options(joinedload(InternalUser.credential))
        .where(InternalUser.email == email)
    )


def revoke_all_user_sessions(db_session: Session, user_id: object, now: datetime) -> None:
    db_session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )


def change_credential_password(
    credential: UserCredential,
    password_service: PasswordService,
    new_password: str,
    now: datetime,
) -> None:
    credential.password_hash = password_service.hash_password(new_password)
    credential.must_change_password = False
    credential.password_changed_at = now
    credential.updated_at = now
