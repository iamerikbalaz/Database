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


def test_auth_security_defaults_and_bounds() -> None:
    settings = Settings(_env_file=None)

    assert settings.auth_argon2_memory_kib >= 19 * 1024
    assert settings.auth_argon2_time_cost >= 2
    assert settings.auth_argon2_parallelism >= 1
    assert settings.auth_idle_timeout_minutes == 30
    assert settings.auth_absolute_timeout_hours == 8
    assert settings.effective_auth_cookie_secure is False
    assert settings.auth_cookie_name == "reawote_session"

    with pytest.raises(ValueError):
        Settings(_env_file=None, auth_argon2_memory_kib=19 * 1024 - 1)


def test_credentialed_cors_rejects_wildcards() -> None:
    with pytest.raises(ValueError, match="wildcards are forbidden"):
        Settings(_env_file=None, cors_origins="*")

    with pytest.raises(ValueError, match="exact HTTP"):
        Settings(_env_file=None, cors_origins="https://example.com/application")


def test_production_requires_secure_cookie_and_https_origin() -> None:
    production = Settings(
        _env_file=None,
        app_env="production",
        cors_origins="https://reawote.example",
    )
    assert production.effective_auth_cookie_secure is True
    assert production.auth_cookie_name == "__Host-reawote_session"

    with pytest.raises(ValueError, match="AUTH_COOKIE_SECURE=true"):
        Settings(
            _env_file=None,
            app_env="production",
            cors_origins="https://reawote.example",
            auth_cookie_secure=False,
        )
    with pytest.raises(ValueError, match="must use HTTPS"):
        Settings(
            _env_file=None,
            app_env="production",
            cors_origins="http://reawote.example",
        )

    with pytest.raises(ValueError, match="allowed only"):
        Settings(
            _env_file=None,
            app_env="staging",
            cors_origins="https://staging.reawote.example",
            auth_cookie_secure=False,
        )
