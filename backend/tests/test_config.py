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
    assert settings.auth_allow_insecure_cookie is False
    assert settings.effective_auth_cookie_secure is True
    assert settings.auth_cookie_name == "__Host-reawote_session"

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
            auth_allow_insecure_cookie=True,
        )


@pytest.mark.parametrize("environment", ["development", "test", "demo", "e2e", "staging"])
def test_app_env_alone_never_disables_secure_cookies(environment: str) -> None:
    settings = Settings(_env_file=None, app_env=environment)

    assert settings.effective_auth_cookie_secure is True
    assert settings.auth_cookie_name == "__Host-reawote_session"
    assert settings.auth_allow_insecure_cookie is False


@pytest.mark.parametrize("environment", ["development", "test", "demo", "e2e"])
@pytest.mark.parametrize(
    "origins",
    [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://[::1]:5173",
        "http://localhost:5173,http://127.0.0.1:5173,http://[::1]:5173",
    ],
)
def test_explicit_insecure_localhost_exception(environment: str, origins: str) -> None:
    settings = Settings(
        _env_file=None,
        app_env=environment,
        cors_origins=origins,
        auth_cookie_secure=False,
        auth_allow_insecure_cookie=True,
    )

    assert settings.effective_auth_cookie_secure is False
    assert settings.auth_cookie_name == "reawote_dev_session"
    assert not settings.auth_cookie_name.startswith("__Host-")


def test_disabling_secure_cookie_requires_separate_opt_in() -> None:
    with pytest.raises(ValueError, match="AUTH_ALLOW_INSECURE_COOKIE=true"):
        Settings(_env_file=None, auth_cookie_secure=False)

    settings = Settings(_env_file=None, auth_allow_insecure_cookie=True)
    assert settings.effective_auth_cookie_secure is True


@pytest.mark.parametrize(
    "origins",
    [
        "http://192.0.2.44",
        "http://192.168.1.20:5173",
        "http://10.0.0.1",
        "http://172.16.0.1",
        "http://server",
        "http://reawote.internal",
        "http://example.com",
        "http://localhost.example.com",
        "http://localhost.",
        "http://127.1",
        "http://[::ffff:127.0.0.1]",
        "http://localhost:5173,https://remote.example",
    ],
)
def test_insecure_cookie_rejects_every_non_loopback_origin(origins: str) -> None:
    with pytest.raises(ValueError, match="loopback-only"):
        Settings(
            _env_file=None,
            cors_origins=origins,
            auth_cookie_secure=False,
            auth_allow_insecure_cookie=True,
        )


@pytest.mark.parametrize(
    "origins",
    [
        "*",
        "http://*.example.com",
        r"\\localhost\share",
        r"http://localhost\remote.example",
        "http://localhost:invalid",
        "http://localhost:65536",
        "http://localhost//",
        "http://local host",
        "http://local\thost",
        "http://user@localhost",
        "http://localhost?target=example.com",
        "http://localhost#example.com",
    ],
)
def test_insecure_cookie_rejects_wildcard_unc_and_malformed_origins(origins: str) -> None:
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            cors_origins=origins,
            auth_cookie_secure=False,
            auth_allow_insecure_cookie=True,
        )


def test_production_cannot_enable_insecure_exception() -> None:
    with pytest.raises(ValueError, match="Production requires AUTH_COOKIE_SECURE=true"):
        Settings(
            _env_file=None,
            app_env="production",
            cors_origins="https://localhost",
            auth_cookie_secure=False,
            auth_allow_insecure_cookie=True,
        )


def test_cookie_name_is_derived_and_cannot_have_an_insecure_host_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Cookie names are intentionally not configurable, including through env.
    monkeypatch.setenv("AUTH_COOKIE_NAME", "__Host-unsafe")
    settings = Settings(
        _env_file=None,
        auth_cookie_secure=False,
        auth_allow_insecure_cookie=True,
        auth_cookie_name="__Host-unsafe",
    )

    assert settings.auth_cookie_name == "reawote_dev_session"
    with pytest.raises(ValueError, match="frozen"):
        settings.auth_cookie_name = "__Host-unsafe"
    with pytest.raises(ValueError, match="frozen"):
        settings.auth_allow_insecure_cookie = False


@pytest.mark.parametrize("interval", [31, 59, 60, 61, 900])
def test_last_seen_interval_leaves_at_least_half_idle_timeout(interval: int) -> None:
    with pytest.raises(ValueError, match="half the idle timeout"):
        Settings(
            _env_file=None,
            auth_idle_timeout_minutes=1,
            auth_last_seen_update_seconds=interval,
        )


@pytest.mark.parametrize("interval", [10, 29, 30])
def test_last_seen_interval_accepts_safe_boundary(interval: int) -> None:
    settings = Settings(
        _env_file=None,
        auth_idle_timeout_minutes=1,
        auth_last_seen_update_seconds=interval,
    )
    assert settings.auth_last_seen_update_seconds == interval


def test_invalid_cookie_configuration_fails_at_application_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("AUTH_ALLOW_INSECURE_COOKIE", "true")
    monkeypatch.setenv("CORS_ORIGINS", "http://192.168.1.20")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="loopback-only"):
            create_app()
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("token", [None, "short", "x" * 257, "x" * 31 + "\n", "x" * 31 + "\x7f", "x" * 31 + "č"])
def test_source_writes_require_valid_private_service_token(token):
    with pytest.raises(ValueError) as caught:
        Settings(_env_file=None, source_mutations_enabled=True, worker_mutation_token=token)
    if token is not None: assert repr(token) not in str(caught.value)


def test_source_writes_are_disabled_by_default_and_token_is_redacted():
    from uuid import uuid4
    token = uuid4().hex + uuid4().hex
    assert Settings(_env_file=None).source_mutations_enabled is False
    settings = Settings(_env_file=None, source_mutations_enabled=True, worker_mutation_token=token)
    assert token not in repr(settings) and token not in settings.model_dump_json()
