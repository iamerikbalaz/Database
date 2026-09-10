from app.core.config import Settings


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
