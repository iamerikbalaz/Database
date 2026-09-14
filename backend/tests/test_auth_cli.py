from __future__ import annotations

import sys
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.auth import cli
from app.auth.cli import AdminProvisioningError, provision_first_admin
from app.auth.security import PasswordService
from app.core.config import Settings
from app.db.base import Base
from app.db.models import InternalUser, UserCredential
from app.db.session import Database
from app.main import create_app


PASSWORD = "Quartz meadow river! 2026"
ORIGIN = "https://localhost"


@pytest.fixture
def database() -> Iterator[Database]:
    database = Database("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(database.engine)
    yield database
    database.dispose()


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, cors_origins=ORIGIN)


@pytest.mark.parametrize(
    ("stored", "submitted"),
    [
        ("admin@example.invalid", "  ADMIN@EXAMPLE.INVALID  "),
        ("groß@example.invalid", "  GROẞ@EXAMPLE.INVALID  "),
        ("e\u0301@example.invalid", "  E\u0301@EXAMPLE.INVALID  "),
    ],
)
def test_cli_reuses_canonical_existing_user_and_account_can_log_in(
    database: Database, settings: Settings, stored: str, submitted: str
) -> None:
    with database.session() as session:
        user = InternalUser(display_name="Pending Account", email=stored, role="PROCESSOR")
        session.add(user)
        session.commit()
        existing_id = user.id
    actual_id = provision_first_admin(
        database, settings, email=submitted, display_name="Site Operator", password=PASSWORD
    )
    assert actual_id == existing_id
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(InternalUser)) == 1
        assert session.get(InternalUser, actual_id).email == stored
    with pytest.raises(AdminProvisioningError):
        provision_first_admin(
            database, settings, email=submitted, display_name="Site Operator", password=PASSWORD
        )
    with TestClient(create_app(settings, database), base_url=ORIGIN) as client:
        for email in (submitted, stored):
            response = client.post(
                "/api/auth/login",
                json={"email": email, "password": PASSWORD},
                headers={"Origin": ORIGIN},
            )
            assert response.status_code == 200
            assert response.json()["user"]["email"] == stored


def test_real_cli_entry_point_uses_getpass_twice_and_provisions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    settings: Settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = Database(f"sqlite+pysqlite:///{(tmp_path / 'cli.sqlite').as_posix()}")
    Base.metadata.create_all(database.engine)
    password = "e\u0301" * 128 + "X"
    answers = iter([password, "é" * 128 + "X"])
    prompts: list[str] = []

    def prompt(message: str) -> str:
        prompts.append(message)
        return next(answers)

    monkeypatch.setattr(sys, "argv", ["first-admin", "--email", "ADMIN@example.invalid",
                                     "--display-name", "Site Operator"])
    monkeypatch.setattr(cli.getpass, "getpass", prompt)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "Database", lambda _: database)
    assert cli.main() == 0
    assert prompts == ["Initial password: ", "Confirm initial password: "]
    with database.session() as session:
        user = session.scalar(select(InternalUser))
        credential = session.scalar(select(UserCredential))
        assert user is not None and user.email == "admin@example.invalid"
        assert credential is not None
        assert PasswordService(settings).verify_password(credential.password_hash, password)
    output = capsys.readouterr()
    assert "First administrator provisioned:" in output.out
    assert password not in output.out + output.err
    database.dispose()


def test_cli_mismatched_confirmation_never_opens_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers = iter([PASSWORD, "Violet harbor lanterns 2048"])
    prompts: list[str] = []

    def prompt(message: str) -> str:
        prompts.append(message)
        return next(answers)

    def unexpected_database(*args):
        pytest.fail("Mismatched confirmation must fail before opening the database")

    monkeypatch.setattr(sys, "argv", ["first-admin", "--email", "admin@example.invalid",
                                     "--display-name", "Site Operator"])
    monkeypatch.setattr(cli.getpass, "getpass", prompt)
    monkeypatch.setattr(cli, "Database", unexpected_database)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert len(prompts) == 2
    output = capsys.readouterr()
    assert "Passwords do not match." in output.err
    assert PASSWORD not in output.out + output.err
    assert "Violet harbor lanterns 2048" not in output.out + output.err


def test_login_does_not_apply_new_password_blocklist_to_existing_hash(
    database: Database, settings: Settings
) -> None:
    # Existing valid credentials retain their login behavior even if the offline
    # list or account-context policy changes after their creation.
    legacy = "correcthorsebatterystaple"
    with database.session() as session:
        user = InternalUser(display_name="Site Operator", email="legacy@example.invalid",
                            role="ADMIN")
        session.add(user)
        session.flush()
        session.add(UserCredential(user_id=user.id,
                                   password_hash=PasswordService(settings).hash_password(legacy)))
        session.commit()
    with TestClient(create_app(settings, database), base_url=ORIGIN) as client:
        response = client.post(
            "/api/auth/login",
            json={"email": "legacy@example.invalid", "password": legacy},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 200
