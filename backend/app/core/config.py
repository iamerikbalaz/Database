from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    app_name: str = "REAWOTE API"
    app_env: str = "development"
    database_url: str | None = None
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "reawote"
    postgres_user: str = "reawote"
    postgres_password: str = "change-me-local-only"
    cors_origins: str = "http://localhost:5173"
    worker_base_url: str = "http://localhost:8080"
    worker_timeout_seconds: float = Field(default=2.0, gt=0, le=10)
    auth_cookie_secure: bool | None = None
    auth_idle_timeout_minutes: int = Field(default=30, ge=1, le=1440)
    auth_absolute_timeout_hours: int = Field(default=8, ge=1, le=168)
    auth_last_seen_update_seconds: int = Field(default=60, ge=10, le=900)
    auth_rate_limit_attempts: int = Field(default=5, ge=1, le=100)
    auth_rate_limit_window_seconds: int = Field(default=300, ge=10, le=3600)
    auth_argon2_memory_kib: int = Field(default=19 * 1024, ge=19 * 1024, le=1024 * 1024)
    auth_argon2_time_cost: int = Field(default=2, ge=2, le=10)
    auth_argon2_parallelism: int = Field(default=1, ge=1, le=16)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def parsed_cors_origins(self) -> list[str]:
        return [
            origin.strip().rstrip("/")
            for origin in self.cors_origins.split(",")
            if origin.strip()
        ]

    @property
    def effective_auth_cookie_secure(self) -> bool:
        if self.auth_cookie_secure is not None:
            return self.auth_cookie_secure
        return self.app_env.casefold() not in {"development", "test", "demo", "e2e"}

    @property
    def auth_cookie_name(self) -> str:
        if self.effective_auth_cookie_secure:
            return "__Host-reawote_session"
        return "reawote_session"

    @model_validator(mode="after")
    def validate_security_configuration(self) -> "Settings":
        origins = self.parsed_cors_origins
        if not origins:
            raise ValueError("CORS_ORIGINS must contain at least one explicit origin")
        if any(origin == "*" or "*" in origin for origin in origins):
            raise ValueError("Credentialed CORS requires explicit origins; wildcards are forbidden")
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "CORS_ORIGINS entries must be exact HTTP(S) origins without "
                    "credentials, path, query, or fragment"
                )
        if self.app_env.casefold() == "production":
            if not self.effective_auth_cookie_secure:
                raise ValueError("Production requires AUTH_COOKIE_SECURE=true")
            if any(not origin.startswith("https://") for origin in origins):
                raise ValueError("Production CORS origins must use HTTPS")
        elif (
            not self.effective_auth_cookie_secure
            and self.app_env.casefold() not in {"development", "test", "demo", "e2e"}
        ):
            raise ValueError(
                "Insecure auth cookies are allowed only in development, test, demo, or e2e"
            )
        return self

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url

        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
