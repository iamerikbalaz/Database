"""Real PostgreSQL lock races and auth constraints (never substituted with SQLite).

Set POSTGRES_TEST_ADMIN_URL to a PostgreSQL account allowed to create databases.
Each module run creates, migrates and drops its own randomly named database.
The races observe pg_blocking_pids before releasing a held credentials row lock;
timeouts bound failures, but elapsed time never establishes request ordering.
"""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import os
from threading import Event
from time import monotonic
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.security import PasswordService, token_digest
from app.core.config import Settings, get_settings
from app.db.models import AuthLoginRateLimit, AuthSession, InternalUser, UserCredential
from app.db.session import Database
from app.main import create_app


POSTGRES_TEST_ADMIN_URL = os.getenv("POSTGRES_TEST_ADMIN_URL")
if POSTGRES_TEST_ADMIN_URL is None and os.getenv("RUN_POSTGRES_TESTS") == "1":
    POSTGRES_TEST_ADMIN_URL = make_url(Settings().resolved_database_url).set(
        database="postgres"
    ).render_as_string(hide_password=False)
pytestmark = pytest.mark.skipif(
    POSTGRES_TEST_ADMIN_URL is None,
    reason="POSTGRES_TEST_ADMIN_URL is required for real PostgreSQL auth integration tests",
)

ORIGIN = "https://localhost:5173"
PASSWORD = "Original forest lantern password 41"
NEW_PASSWORD = "Changed mountain lantern password 52"
SECOND_PASSWORD = "Another river lantern password 63"
TIMEOUT = 20


@pytest.fixture(scope="module")
def auth_postgresql_url() -> Iterator[str]:
    assert POSTGRES_TEST_ADMIN_URL is not None
    admin_url = make_url(POSTGRES_TEST_ADMIN_URL)
    assert admin_url.get_backend_name() == "postgresql"
    database_name = f"reawote_auth_test_{uuid4().hex}"
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    database_url = admin_url.set(database=database_name).render_as_string(hide_password=False)
    previous_database_url = os.environ.get("DATABASE_URL")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        os.environ["DATABASE_URL"] = database_url
        get_settings.cache_clear()
        command.upgrade(Config("alembic.ini"), "head")
        yield database_url
    finally:
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        get_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
        admin_engine.dispose()


class _ReusableDatabase(Database):
    """Check the real context manager's cleanup after both return and exception."""

    def __init__(self, url: str) -> None:
        super().__init__(url)
        self.reuse_checks: list[bool] = []

    @contextmanager
    def session(self) -> Iterator[Session]:
        db_session = None
        try:
            with super().session() as db_session:
                yield db_session
        finally:
            if db_session is not None:
                # Production close/rollback already ran. Reopen the same Session,
                # not just a separate observer connection, after success or 4xx.
                assert not db_session.in_transaction()
                try:
                    assert db_session.scalar(text("SELECT 1")) == 1
                    self.reuse_checks.append(True)
                finally:
                    db_session.close()


@dataclass
class _LockObservation:
    hold: bool = False
    require_session_before_commit: bool = False
    attempted: Event = field(default_factory=Event)
    acquired: Event = field(default_factory=Event)
    release: Event = field(default_factory=Event)
    backend_pid: int | None = None
    session_inserted: bool = False
    credential_transaction_committed: bool = False

    @staticmethod
    def _credentials_lock(statement: str) -> bool:
        # Auth-context SELECT also joins credentials but locks only auth_sessions.
        # Only the SELECT rooted at user_credentials establishes this row lock.
        return "FROM USER_CREDENTIALS" in statement.upper() and "FOR UPDATE" in statement.upper()

    def before(self, connection: Connection, cursor, statement: str, *args) -> None:
        if self._credentials_lock(statement):
            self.backend_pid = connection.connection.driver_connection.info.backend_pid
            self.attempted.set()

    def after(self, connection: Connection, cursor, statement: str, *args) -> None:
        if statement.upper().startswith("INSERT INTO AUTH_SESSIONS"):
            self.session_inserted = True
        if self._credentials_lock(statement):
            self.acquired.set()
            if self.hold and not self.release.wait(timeout=TIMEOUT):
                raise TimeoutError("Timed out holding the credentials row lock")

    def commit(self, connection: Connection) -> None:
        if self.acquired.is_set() and not self.credential_transaction_committed:
            if self.require_session_before_commit:
                assert self.session_inserted, "Credentials lock released before session INSERT"
            self.credential_transaction_committed = True

    def attach(self, database: Database) -> None:
        event.listen(database.engine, "before_cursor_execute", self.before)
        event.listen(database.engine, "after_cursor_execute", self.after)
        event.listen(database.engine, "commit", self.commit)


@dataclass
class _AuthCase:
    url: str
    user_id: UUID
    email: str
    clients: tuple[TestClient, TestClient]
    databases: tuple[_ReusableDatabase, _ReusableDatabase]
    csrf_tokens: tuple[str, str]


@pytest.fixture
def auth_case(auth_postgresql_url: str) -> Iterator[_AuthCase]:
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=auth_postgresql_url,
        cors_origins=ORIGIN,
        auth_cookie_secure=True,
        auth_rate_limit_attempts=100,
    )
    databases = (_ReusableDatabase(auth_postgresql_url), _ReusableDatabase(auth_postgresql_url))
    email = f"concurrency-{uuid4().hex}@example.invalid"
    with databases[0].session() as db_session:
        user = InternalUser(display_name="Concurrency User", email=email, role="ADMIN")
        db_session.add(user)
        db_session.flush()
        user_id = user.id
        db_session.add(
            UserCredential(
                user_id=user_id,
                password_hash=PasswordService(settings).hash_password(PASSWORD),
                must_change_password=True,
            )
        )
        db_session.commit()
    try:
        with (
            TestClient(
                create_app(settings, databases[0]),
                base_url=ORIGIN,
                raise_server_exceptions=False,
            ) as first,
            TestClient(
                create_app(settings, databases[1]),
                base_url=ORIGIN,
                raise_server_exceptions=False,
            ) as second,
        ):
            csrf_tokens = []
            for client in (first, second):
                response = client.post(
                    "/api/auth/login",
                    json={"email": email, "password": PASSWORD},
                    headers={"Origin": ORIGIN},
                )
                assert response.status_code == 200, response.text
                csrf_tokens.append(response.json()["csrf_token"])
            yield _AuthCase(
                auth_postgresql_url,
                user_id,
                email,
                (first, second),
                databases,
                (csrf_tokens[0], csrf_tokens[1]),
            )
    finally:
        for database in databases:
            database.dispose()


def _wait_until_postgresql_confirms_blocking(
    url: str, holder: _LockObservation, waiter: _LockObservation
) -> None:
    assert waiter.attempted.wait(timeout=TIMEOUT), "Second request never attempted row lock"
    assert holder.backend_pid is not None and waiter.backend_pid is not None
    assert holder.backend_pid != waiter.backend_pid
    observer = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        deadline = monotonic() + TIMEOUT
        with observer.connect() as connection:
            while monotonic() < deadline:
                blockers = connection.scalar(
                    text("SELECT pg_blocking_pids(:pid)"), {"pid": waiter.backend_pid}
                )
                if holder.backend_pid in blockers:
                    assert not waiter.acquired.is_set()
                    return
                assert not waiter.acquired.is_set(), "Credentials SELECT did not serialize"
        pytest.fail("PostgreSQL never reported the second request blocked by the first")
    finally:
        observer.dispose()


def _request(case: _AuthCase, index: int, action: str) -> httpx.Response:
    headers = {"Origin": ORIGIN}
    if action == "login":
        payload = {"email": case.email, "password": PASSWORD}
    else:
        payload = {
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD if index == 0 else SECOND_PASSWORD,
        }
        headers["X-CSRF-Token"] = case.csrf_tokens[index]
    return case.clients[index].post(f"/api/auth/{action}", json=payload, headers=headers)


def _run_race(case: _AuthCase, first_action: str, second_action: str) -> list[httpx.Response]:
    holder = _LockObservation(hold=True, require_session_before_commit=first_action == "login")
    waiter = _LockObservation()
    holder.attach(case.databases[0])
    waiter.attach(case.databases[1])
    reuse_before = [len(database.reuse_checks) for database in case.databases]
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(_request, case, 0, first_action)
        try:
            assert holder.acquired.wait(timeout=TIMEOUT), "First request never locked credentials"
            second_future = executor.submit(_request, case, 1, second_action)
            _wait_until_postgresql_confirms_blocking(case.url, holder, waiter)
        finally:
            holder.release.set()
        responses = [first_future.result(timeout=TIMEOUT), second_future.result(timeout=TIMEOUT)]
    assert waiter.acquired.is_set()
    assert all(response.status_code != 500 for response in responses)
    for database, previous_count in zip(case.databases, reuse_before, strict=True):
        assert len(database.reuse_checks) > previous_count
        assert database.ping(), "Backend connection must remain usable after the race"
    return responses


def _assert_final_state(case: _AuthCase, password: str, expected_sessions: int) -> list[AuthSession]:
    with case.databases[0].session() as db_session:
        credential = db_session.get(UserCredential, case.user_id)
        sessions = list(
            db_session.scalars(select(AuthSession).where(AuthSession.user_id == case.user_id))
        )
        assert credential is not None
        assert not credential.must_change_password
        assert credential.updated_at == credential.password_changed_at
        assert credential.password_changed_at >= credential.created_at
        assert PasswordService(Settings(_env_file=None)).verify_password(
            credential.password_hash, password
        )
        assert not PasswordService(Settings(_env_file=None)).verify_password(
            credential.password_hash, PASSWORD
        )
        assert len(sessions) == expected_sessions
        for stored in sessions:
            assert stored.revoked_at is not None
            assert stored.revoked_at >= stored.created_at
            assert stored.created_at.tzinfo is not None
            assert stored.revoked_at.tzinfo is not None
            assert stored.revoked_at >= credential.password_changed_at
        return sessions


def test_postgresql_login_lock_then_password_change_revokes_new_session(auth_case: _AuthCase) -> None:
    responses = _run_race(auth_case, "login", "change-password")
    assert [response.status_code for response in responses] == [200, 200]
    sessions = _assert_final_state(auth_case, SECOND_PASSWORD, expected_sessions=3)
    new_token = responses[0].cookies.get("__Host-reawote_session")
    assert new_token is not None
    assert any(item.token_hash == token_digest(new_token) for item in sessions)
    assert auth_case.clients[0].get("/api/auth/session").status_code == 401


def test_postgresql_password_change_lock_then_old_login_fails(auth_case: _AuthCase) -> None:
    responses = _run_race(auth_case, "change-password", "login")
    assert [response.status_code for response in responses] == [200, 401]
    assert responses[1].json() == {"detail": "Invalid email or password."}
    _assert_final_state(auth_case, NEW_PASSWORD, expected_sessions=2)


def test_postgresql_two_password_changes_only_first_accepts_old_password(auth_case: _AuthCase) -> None:
    responses = _run_race(auth_case, "change-password", "change-password")
    assert [response.status_code for response in responses] == [200, 400]
    assert responses[1].json() == {"detail": "Current password is incorrect."}
    _assert_final_state(auth_case, NEW_PASSWORD, expected_sessions=2)


@pytest.fixture
def constraint_session(auth_postgresql_url: str) -> Iterator[tuple[Session, UUID]]:
    engine = create_engine(auth_postgresql_url)
    try:
        with Session(engine) as db_session:
            user = InternalUser(
                display_name="Constraint User",
                email=f"constraint-{uuid4().hex}@example.invalid",
                role="ADMIN",
            )
            db_session.add(user)
            db_session.commit()
            yield db_session, user.id
    finally:
        engine.dispose()


def _session_values(user_id: UUID) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "id": uuid4(),
        "user_id": user_id,
        "token_hash": uuid4().hex + uuid4().hex,
        "csrf_token": "c" * 43,
        "created_at": now,
        "last_seen_at": now,
        "idle_expires_at": now + timedelta(minutes=30),
        "absolute_expires_at": now + timedelta(hours=8),
        "revoked_at": None,
    }


def _assert_constraint_rejection(db_session: Session, instance, expected_constraint: str) -> None:
    db_session.add(instance)
    with pytest.raises(IntegrityError) as error:
        db_session.commit()
    assert error.value.orig.diag.constraint_name == expected_constraint
    db_session.rollback()
    assert db_session.scalar(text("SELECT 1")) == 1
    db_session.commit()


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "constraint"),
    [
        ("token_hash", "short", "ck_auth_sessions_token_hash"),
        ("csrf_token", "short", "ck_auth_sessions_csrf_token"),
        ("absolute_expires_at", "created_at", "ck_auth_sessions_absolute_expiration"),
        ("idle_expires_at", "created_at", "ck_auth_sessions_idle_expiration"),
        ("idle_expires_at", "after_absolute", "ck_auth_sessions_idle_expiration"),
        ("last_seen_at", "before_created", "ck_auth_sessions_last_seen"),
        ("revoked_at", "before_created", "ck_auth_sessions_revoked_at"),
    ],
)
def test_postgresql_auth_session_check_constraints(
    constraint_session: tuple[Session, UUID],
    field_name: str,
    invalid_value: str,
    constraint: str,
) -> None:
    db_session, user_id = constraint_session
    values = _session_values(user_id)
    replacements = {
        "created_at": values["created_at"],
        "before_created": values["created_at"] - timedelta(microseconds=1),
        "after_absolute": values["absolute_expires_at"] + timedelta(microseconds=1),
    }
    values[field_name] = replacements.get(invalid_value, invalid_value)
    _assert_constraint_rejection(db_session, AuthSession(**values), constraint)


@pytest.mark.parametrize("model", [UserCredential, AuthSession])
def test_postgresql_auth_foreign_keys(constraint_session: tuple[Session, UUID], model) -> None:
    db_session, _ = constraint_session
    missing_user = uuid4()
    instance = (
        UserCredential(user_id=missing_user, password_hash="constraint-test-only")
        if model is UserCredential
        else AuthSession(**_session_values(missing_user))
    )
    _assert_constraint_rejection(db_session, instance, f"{model.__tablename__}_user_id_fkey")


def test_postgresql_unique_credential_and_session_token(
    constraint_session: tuple[Session, UUID],
) -> None:
    db_session, user_id = constraint_session
    values = _session_values(user_id)
    db_session.add_all(
        [
            UserCredential(user_id=user_id, password_hash="constraint-test-only"),
            AuthSession(**values),
        ]
    )
    db_session.commit()
    _assert_constraint_rejection(
        db_session,
        UserCredential(user_id=user_id, password_hash="duplicate-constraint-test-only"),
        "user_credentials_pkey",
    )
    values["id"] = uuid4()
    _assert_constraint_rejection(
        db_session, AuthSession(**values), "uq_auth_sessions_token_hash"
    )


@pytest.mark.parametrize(
    ("key_hash", "attempt_count", "constraint"),
    [
        ("short", 1, "ck_auth_login_rate_limits_key_hash"),
        ("a" * 64, 0, "ck_auth_login_rate_limits_attempt_count"),
        ("b" * 64, -1, "ck_auth_login_rate_limits_attempt_count"),
    ],
)
def test_postgresql_rate_limit_check_constraints(
    constraint_session: tuple[Session, UUID],
    key_hash: str,
    attempt_count: int,
    constraint: str,
) -> None:
    db_session, _ = constraint_session
    _assert_constraint_rejection(
        db_session,
        AuthLoginRateLimit(key_hash=key_hash, window_bucket=1, attempt_count=attempt_count),
        constraint,
    )


def test_postgresql_rate_limit_bucket_uniqueness(constraint_session: tuple[Session, UUID]) -> None:
    db_session, _ = constraint_session
    key_hash = uuid4().hex + uuid4().hex
    db_session.add(AuthLoginRateLimit(key_hash=key_hash, window_bucket=1, attempt_count=1))
    db_session.commit()
    _assert_constraint_rejection(
        db_session,
        AuthLoginRateLimit(key_hash=key_hash, window_bucket=1, attempt_count=2),
        "auth_login_rate_limits_pkey",
    )
    db_session.add(AuthLoginRateLimit(key_hash=key_hash, window_bucket=2, attempt_count=1))
    db_session.commit()


def test_postgresql_auth_user_delete_cascades(constraint_session: tuple[Session, UUID]) -> None:
    db_session, user_id = constraint_session
    db_session.add_all(
        [
            UserCredential(user_id=user_id, password_hash="constraint-test-only"),
            AuthSession(**_session_values(user_id)),
        ]
    )
    db_session.commit()
    # Raw SQL exercises the migrated database FK, not ORM cascade behavior.
    db_session.execute(text("DELETE FROM internal_users WHERE id = :id"), {"id": user_id})
    db_session.commit()
    assert db_session.get(UserCredential, user_id) is None
    assert db_session.scalar(select(AuthSession).where(AuthSession.user_id == user_id)) is None
    assert db_session.scalar(text("SELECT 1")) == 1
