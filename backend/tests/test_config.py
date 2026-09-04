from app.core.config import Settings


def test_database_url_escapes_credentials() -> None:
    settings = Settings(postgres_password="local@password:with/slashes")

    assert "local%40password%3Awith%2Fslashes" in settings.resolved_database_url
