from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from argon2 import extract_parameters
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.cli import AdminProvisioningError, provision_first_admin
from app.auth.dependencies import require_roles
from app.auth.security import (
    PasswordPolicyError,
    PasswordService,
    normalize_password,
    token_digest,
    validate_new_password,
)
from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    AuthSession,
    InternalUser,
    InternalUserRole,
    UserCredential,
)
from app.db.session import Database
from app.main import create_app


PASSWORD = "Quartz meadow river! 2026"
NEW_PASSWORD = "Violet harbor lanterns 2048"
ORIGIN = "http://testserver"


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings(_env_file=None, cors_origins=ORIGIN)


@pytest.fixture(scope="module")
def password_service(settings: Settings) -> PasswordService:
    return PasswordService(settings)


@pytest.fixture
def auth_client(settings: Settings) -> Iterator[tuple[TestClient, Database]]:
    database = Database("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(database.engine)
    application = create_app(settings, database)
    with TestClient(application, base_url=ORIGIN) as client:
        yield client, database


def create_account(
    database: Database,
    password_service: PasswordService,
    *,
    email: str = "admin@example.com",
    password: str = PASSWORD,
    active: bool = True,
    with_credentials: bool = True,
    must_change_password: bool = True,
) -> InternalUser:
    with database.session() as db_session:
        user = InternalUser(
            display_name="Platform Administrator",
            email=email,
            role=InternalUserRole.ADMIN.value,
            is_active=active,
        )
        db_session.add(user)
        db_session.flush()
        if with_credentials:
            db_session.add(
                UserCredential(
                    user_id=user.id,
                    password_hash=password_service.hash_password(password),
                    must_change_password=must_change_password,
                )
            )
        db_session.commit()
        db_session.refresh(user)
        return user


def login(
    client: TestClient,
    *,
    email: str = "admin@example.com",
    password: str = PASSWORD,
    origin: str | None = ORIGIN,
):
    headers = {"Origin": origin} if origin is not None else {}
    return client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
        headers=headers,
    )


def csrf_headers(csrf_token: str, origin: str = ORIGIN) -> dict[str, str]:
    return {"Origin": origin, "X-CSRF-Token": csrf_token}


def test_argon2id_hash_configuration_and_unicode_normalization(
    settings: Settings,
    password_service: PasswordService,
) -> None:
    password = "  dlouhé heslo s Unicode žluťoučký  "
    composed = normalize_password(password)
    password_hash = password_service.hash_password(password)
    parameters = extract_parameters(password_hash)

    assert password_hash.startswith("$argon2id$")
    assert parameters.memory_cost == settings.auth_argon2_memory_kib
    assert parameters.time_cost == settings.auth_argon2_time_cost
    assert parameters.parallelism == settings.auth_argon2_parallelism
    assert password_service.verify_password(password_hash, composed)
    assert password not in password_hash


def test_password_policy_accepts_spaces_unicode_and_64_characters() -> None:
    candidate = "ž" * 64 + " with spaces"
    assert validate_new_password(
        candidate,
        email="someone@example.com",
        display_name="Different Person",
    ) == candidate


@pytest.mark.parametrize(
    "candidate",
    [
        "too short",
        "correcthorsebatterystaple",
        "xxxxxxxxxxxxxxxx",
        "admin@example.com has a long password",
        "REAWOTE is inside this password",
    ],
)
def test_password_policy_rejects_weak_compromised_and_contextual_passwords(
    candidate: str,
) -> None:
    with pytest.raises(PasswordPolicyError):
        validate_new_password(
            candidate,
            email="admin@example.com",
            display_name="Platform Administrator",
        )


def test_login_success_returns_public_profile_and_session_cookie(
    auth_client: tuple[TestClient, Database],
    password_service: PasswordService,
    settings: Settings,
) -> None:
    client, database = auth_client
    user = create_account(database, password_service)

    response = login(client, email="  ＡＤＭＩＮ＠ＥＸＡＭＰＬＥ．ＣＯＭ  ")

    assert response.status_code == 200
    body = response.json()
    assert body["user"] == {
        "id": str(user.id),
        "display_name": "Platform Administrator",
        "email": "admin@example.com",
        "role": "ADMIN",
    }
    assert body["must_change_password"] is True
    assert body["csrf_token"]
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"]
    assert "reawote_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/" in cookie
    assert "Domain=" not in cookie
    assert "Max-Age" not in cookie
    assert "Expires=" not in cookie


def test_production_cookie_is_secure_host_cookie(
    password_service: PasswordService,
) -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        cors_origins="https://testserver",
    )
    database = Database("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(database.engine)
    create_account(database, password_service)
    with TestClient(create_app(settings, database), base_url="https://testserver") as client:
        response = login(client, origin="https://testserver")

    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-reawote_session=")
    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/" in cookie
    assert "Domain=" not in cookie


@pytest.mark.parametrize(
    ("account_kwargs", "email", "password"),
    [
        ({}, "admin@example.com", "incorrect password value"),
        ({}, "missing@example.com", "incorrect password value"),
        ({"with_credentials": False}, "admin@example.com", "incorrect password value"),
        ({"active": False}, "admin@example.com", PASSWORD),
    ],
    ids=["wrong-password", "missing-user", "missing-credentials", "inactive"],
)
def test_login_failures_do_not_enumerate_accounts(
    auth_client: tuple[TestClient, Database],
    password_service: PasswordService,
    account_kwargs: dict[str, object],
    email: str,
    password: str,
) -> None:
    client, database = auth_client
    if email != "missing@example.com":
        create_account(database, password_service, **account_kwargs)

    response = login(client, email=email, password=password)

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password."}
    assert response.headers["cache-control"] == "no-store"


def test_login_requires_valid_input_and_login_csrf_origin(
    auth_client: tuple[TestClient, Database],
) -> None:
    client, _ = auth_client
    assert login(client, email="not-an-email").status_code == 422
    assert login(client, origin="https://attacker.example").status_code == 403
    missing = login(client, origin=None)
    assert missing.status_code == 403
    assert missing.json()["detail"] == "Missing request origin proof."


def test_database_backed_rate_limit_returns_controlled_429(
    password_service: PasswordService,
) -> None:
    settings = Settings(
        _env_file=None,
        cors_origins=ORIGIN,
        auth_rate_limit_attempts=2,
        auth_rate_limit_window_seconds=60,
    )
    database = Database("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(database.engine)
    create_account(database, password_service)
    with TestClient(create_app(settings, database), base_url=ORIGIN) as client:
        assert login(client, password="wrong one is long enough").status_code == 401
        assert login(client, password="wrong two is long enough").status_code == 401
        limited = login(client, password="wrong three is long enough")
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"
    assert limited.json() == {"detail": "Too many login attempts. Try again later."}


def test_raw_session_token_is_only_in_cookie_and_hash_is_in_database(
    auth_client: tuple[TestClient, Database],
    password_service: PasswordService,
    settings: Settings,
) -> None:
    client, database = auth_client
    create_account(database, password_service)
    response = login(client)
    raw_token = client.cookies.get(settings.auth_cookie_name)
    assert raw_token is not None
    assert raw_token not in response.text

    with database.session() as db_session:
        stored = db_session.scalar(select(AuthSession))
        credential = db_session.scalar(select(UserCredential))
    assert stored is not None
    assert stored.token_hash == token_digest(raw_token)
    assert stored.token_hash != raw_token
    assert credential is not None
    assert credential.password_hash != PASSWORD
    assert PASSWORD not in credential.password_hash


def test_session_token_is_not_accepted_outside_cookie(
    auth_client: tuple[TestClient, Database],
) -> None:
    client, _ = auth_client
    client.cookies.clear()
    fake_token = "A" * 43
    assert client.get(
        "/api/auth/session",
        params={"session_token": fake_token},
        headers={"Authorization": f"Bearer {fake_token}"},
    ).status_code == 401
    assert client.post(
        "/api/auth/logout",
        json={"session_token": fake_token},
        headers=csrf_headers("B" * 43),
    ).status_code == 401


def test_credentialed_cors_uses_exact_origin(
    auth_client: tuple[TestClient, Database],
) -> None:
    client, _ = auth_client
    response = client.options(
        "/api/auth/login",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"


def test_current_session_and_throttled_last_seen_update(
    auth_client: tuple[TestClient, Database],
    password_service: PasswordService,
) -> None:
    client, database = auth_client
    create_account(database, password_service)
    logged_in = login(client).json()

    response = client.get("/api/auth/session")
    assert response.status_code == 200
    assert response.json() == logged_in
    assert response.headers["cache-control"] == "no-store"

    old = datetime.now(UTC) - timedelta(minutes=2)
    with database.session() as db_session:
        stored = db_session.scalar(select(AuthSession))
        assert stored is not None
        stored.created_at = old - timedelta(minutes=1)
        stored.last_seen_at = old
        db_session.commit()
    assert client.get("/api/auth/session").status_code == 200
    with database.session() as db_session:
        refreshed = db_session.scalar(select(AuthSession))
        assert refreshed is not None
        assert refreshed.last_seen_at.replace(tzinfo=UTC) > old


@pytest.mark.parametrize("expiration_field", ["idle_expires_at", "absolute_expires_at"])
def test_expired_session_is_revoked_and_returns_401(
    auth_client: tuple[TestClient, Database],
    password_service: PasswordService,
    expiration_field: str,
) -> None:
    client, database = auth_client
    create_account(database, password_service)
    assert login(client).status_code == 200
    now = datetime.now(UTC)
    with database.session() as db_session:
        stored = db_session.scalar(select(AuthSession))
        assert stored is not None
        stored.created_at = now - timedelta(hours=10)
        expiration = now - timedelta(seconds=1)
        setattr(stored, expiration_field, expiration)
        if expiration_field == "absolute_expires_at":
            stored.idle_expires_at = now - timedelta(seconds=2)
        db_session.commit()

    assert client.get("/api/auth/session").status_code == 401
    with database.session() as db_session:
        stored = db_session.scalar(select(AuthSession))
        assert stored is not None and stored.revoked_at is not None


def test_revoked_session_and_user_deactivation_return_401(
    auth_client: tuple[TestClient, Database],
    password_service: PasswordService,
) -> None:
    client, database = auth_client
    user = create_account(database, password_service)
    assert login(client).status_code == 200
    with database.session() as db_session:
        stored = db_session.scalar(select(AuthSession))
        assert stored is not None
        stored.revoked_at = datetime.now(UTC)
        db_session.commit()
    assert client.get("/api/auth/session").status_code == 401

    client.cookies.clear()
    assert login(client).status_code == 200
    with database.session() as db_session:
        stored_user = db_session.get(InternalUser, user.id)
        assert stored_user is not None
        stored_user.is_active = False
        db_session.commit()
    assert client.get("/api/auth/session").status_code == 401


def test_logout_requires_csrf_revokes_session_and_deletes_cookie(
    auth_client: tuple[TestClient, Database],
    password_service: PasswordService,
) -> None:
    client, database = auth_client
    create_account(database, password_service)
    csrf = login(client).json()["csrf_token"]

    assert client.post("/api/auth/logout", headers={"Origin": ORIGIN}).status_code == 403
    assert client.post(
        "/api/auth/logout", headers=csrf_headers("wrong-csrf-token")
    ).status_code == 403
    assert client.post(
        "/api/auth/logout", headers=csrf_headers(csrf, "https://attacker.example")
    ).status_code == 403

    response = client.post("/api/auth/logout", headers=csrf_headers(csrf))
    assert response.status_code == 200
    assert response.json() == {"status": "logged_out"}
    assert "Max-Age=0" in response.headers["set-cookie"]
    with database.session() as db_session:
        stored = db_session.scalar(select(AuthSession))
        assert stored is not None and stored.revoked_at is not None
    assert client.get("/api/auth/session").status_code == 401
    assert client.post("/api/auth/logout", headers=csrf_headers(csrf)).status_code == 401


def test_change_password_clears_flag_and_revokes_all_sessions(
    auth_client: tuple[TestClient, Database],
    password_service: PasswordService,
) -> None:
    client, database = auth_client
    user = create_account(database, password_service)
    first_login = login(client)
    csrf = first_login.json()["csrf_token"]
    second_client = TestClient(client.app, base_url=ORIGIN)
    assert login(second_client).status_code == 200

    wrong = client.post(
        "/api/auth/change-password",
        json={"current_password": "wrong current password", "new_password": NEW_PASSWORD},
        headers=csrf_headers(csrf),
    )
    assert wrong.status_code == 400

    response = client.post(
        "/api/auth/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers=csrf_headers(csrf),
    )
    assert response.status_code == 200
    assert response.json()["reauthentication_required"] is True
    assert "Max-Age=0" in response.headers["set-cookie"]

    with database.session() as db_session:
        credential = db_session.get(UserCredential, user.id)
        sessions = list(db_session.scalars(select(AuthSession)))
    assert credential is not None
    assert credential.must_change_password is False
    assert password_service.verify_password(credential.password_hash, NEW_PASSWORD)
    assert sessions and all(item.revoked_at is not None for item in sessions)
    assert client.get("/api/auth/session").status_code == 401
    assert second_client.get("/api/auth/session").status_code == 401
    assert login(client, password=PASSWORD).status_code == 401
    assert login(client, password=NEW_PASSWORD).status_code == 200
    second_client.close()


def test_auth_logs_and_api_do_not_contain_password_hash_or_session_token(
    auth_client: tuple[TestClient, Database],
    password_service: PasswordService,
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, database = auth_client
    create_account(database, password_service)
    caplog.set_level(logging.INFO, logger="reawote.auth")
    response = login(client)
    raw_token = client.cookies.get(settings.auth_cookie_name)
    with database.session() as db_session:
        credential = db_session.scalar(select(UserCredential))
    assert credential is not None and raw_token is not None
    combined = response.text + caplog.text
    assert PASSWORD not in combined
    assert credential.password_hash not in combined
    assert raw_token not in combined


def test_first_admin_provisioning_is_safe_and_one_time(
    settings: Settings,
    password_service: PasswordService,
) -> None:
    database = Database("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(database.engine)
    with database.session() as db_session:
        existing = InternalUser(
            display_name="Pending User",
            email="first@example.com",
            role=InternalUserRole.PROCESSOR.value,
            is_active=False,
        )
        db_session.add(existing)
        db_session.commit()
        existing_id = existing.id

    user_id = provision_first_admin(
        database,
        settings,
        email="FIRST@EXAMPLE.COM",
        display_name="First Administrator",
        password=PASSWORD,
    )
    assert user_id == existing_id
    with database.session() as db_session:
        user = db_session.get(InternalUser, user_id)
        credential = db_session.get(UserCredential, user_id)
    assert user is not None and user.role == InternalUserRole.ADMIN.value and user.is_active
    assert credential is not None and credential.must_change_password
    assert password_service.verify_password(credential.password_hash, PASSWORD)
    with pytest.raises(AdminProvisioningError):
        provision_first_admin(
            database,
            settings,
            email="second@example.com",
            display_name="Second Administrator",
            password=NEW_PASSWORD,
        )


def test_role_dependency_is_reusable(
    auth_client: tuple[TestClient, Database],
    password_service: PasswordService,
) -> None:
    client, database = auth_client
    create_account(database, password_service)
    protected = FastAPI()
    protected.state.database = database
    protected.state.settings = Settings(_env_file=None, cors_origins=ORIGIN)

    @protected.get("/admin")
    def admin_only(_=Depends(require_roles(InternalUserRole.ADMIN))):
        return {"ok": True}

    auth_response = login(client)
    raw_cookie = client.cookies.get("reawote_session")
    with TestClient(protected, base_url=ORIGIN) as protected_client:
        protected_client.cookies.set("reawote_session", raw_cookie)
        assert protected_client.get("/admin").json() == {"ok": True}
