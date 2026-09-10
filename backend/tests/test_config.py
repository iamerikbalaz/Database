import pytest

from app.core.config import Settings


def test_default_worker_url_uses_worker_http_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WORKER_BASE_URL", raising=False)

    assert Settings(_env_file=None).worker_base_url == "http://localhost:8080"


def test_worker_url_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_BASE_URL", "http://worker:8080")

    assert Settings(_env_file=None).worker_base_url == "http://worker:8080"


def test_database_url_escapes_credentials() -> None:
    settings = Settings(postgres_password="local@password:with/slashes")

    assert "local%40password%3Awith%2Fslashes" in settings.resolved_database_url


def test_worker_settings_are_configurable_and_bounded() -> None:
    settings = Settings(
        worker_base_url="https://worker.internal.example",
        worker_timeout_seconds=1.25,
    )

    assert settings.worker_base_url == "https://worker.internal.example"
    assert settings.worker_timeout_seconds == 1.25
