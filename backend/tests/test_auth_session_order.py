"""Supplemental session recheck tests; PostgreSQL tests prove actual lock races."""

from collections.abc import Iterator
from datetime import timedelta

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, text

from app.auth.dependencies import require_active_user
from app.auth import router as auth_router
from app.auth.security import PasswordService
from app.auth.service import AuthContext, create_session, database_now
from app.core.config import Settings
from app.db.base import Base
from app.db.models import AuthSession, InternalUser, UserCredential
from app.db.session import Database
from app.main import create_app


ORIGIN = "https://localhost:5173"
PASSWORD = "Original forest lantern password 41"
NEW_PASSWORD = "Changed mountain lantern password 52"


@pytest.fixture
def stale_auth_client() -> Iterator[tuple[TestClient, Database, AuthContext]]:
    settings = Settings(_env_file=None, app_env="test", cors_origins=ORIGIN)
    database = Database("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(database.engine)
    with database.session() as db_session:
        user = InternalUser(
            display_name="Session Recheck User", email="recheck@example.invalid", role="ADMIN"
        )
        db_session.add(user)
        db_session.flush()
        db_session.add(
            UserCredential(
                user_id=user.id,
                password_hash=PasswordService(settings).hash_password(PASSWORD),
                must_change_password=True,
            )
        )
        auth_session, _, _ = create_session(
            db_session, settings, user, now=database_now(db_session) - timedelta(minutes=5)
        )
        db_session.commit()
        context = AuthContext(user=user, session=auth_session)
    application = create_app(settings, database)
    # Represent authentication that completed before a credentials-lock wait.
    # The route must independently load the subsequently changed database row.
    application.dependency_overrides[require_active_user] = lambda: context
    try:
        with TestClient(application, base_url=ORIGIN, raise_server_exceptions=False) as client:
            yield client, database, context
    finally:
        database.dispose()


@pytest.mark.parametrize("state", ["revoked", "idle-expired", "absolute-expired", "missing"])
@pytest.mark.parametrize(
    "current_password",
    [PASSWORD, "Unrelated incorrect password 74"],
    ids=["correct-password", "wrong-password"],
)
def test_session_invalid_after_initial_authentication_never_verifies_password(
    stale_auth_client: tuple[TestClient, Database, AuthContext],
    state: str,
    current_password: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, database, context = stale_auth_client
    with database.session() as db_session:
        stored = db_session.get(AuthSession, context.session.id)
        credential = db_session.get(UserCredential, context.user.id)
        assert stored is not None and credential is not None
        previous_hash = credential.password_hash
        previous_changed_at = credential.password_changed_at
        now = database_now(db_session)
        if state == "revoked":
            stored.revoked_at = now
        elif state == "idle-expired":
            stored.idle_expires_at = now - timedelta(seconds=1)
        elif state == "absolute-expired":
            stored.idle_expires_at = now - timedelta(seconds=2)
            stored.absolute_expires_at = now - timedelta(seconds=1)
        else:
            db_session.delete(stored)
        db_session.commit()

    lock_calls = []
    lock_credential = auth_router.lock_user_credential

    def observe_lock(db_session, user_id):
        lock_calls.append(user_id)
        return lock_credential(db_session, user_id)

    def reject_password_verification(*args, **kwargs):
        pytest.fail("Invalidated session must be rejected before any password verification")

    monkeypatch.setattr(auth_router, "lock_user_credential", observe_lock)
    monkeypatch.setattr(PasswordService, "verify_password", reject_password_verification)
    response = client.post(
        "/api/auth/change-password",
        json={"current_password": current_password, "new_password": NEW_PASSWORD},
        headers={"Origin": ORIGIN, "X-CSRF-Token": context.session.csrf_token},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated."}
    assert response.headers["cache-control"] == "no-store"
    assert lock_calls == [context.user.id]
    with database.session() as db_session:
        credential = db_session.scalar(select(UserCredential))
        assert credential is not None
        assert credential.password_hash == previous_hash
        assert credential.password_changed_at == previous_changed_at
        assert credential.must_change_password
        assert db_session.scalar(text("SELECT 1")) == 1
    assert database.ping()
