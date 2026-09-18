from functools import lru_cache
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, model_validator
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
    zip_policy_timezone: str = Field(default="Europe/Prague", min_length=1, max_length=100)
    source_mutations_enabled: bool = False
    worker_mutation_token: SecretStr | None = None
    auth_cookie_secure: bool = True
    auth_allow_insecure_cookie: bool = False
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
        frozen=True,
        hide_input_in_errors=True,
    )

    @property
    def parsed_cors_origins(self) -> list[str]:
        return [
            origin.strip().removesuffix("/")
            for origin in self.cors_origins.split(",")
            if origin.strip()
        ]

    @property
    def effective_auth_cookie_secure(self) -> bool:
        return self.auth_cookie_secure

    @property
    def auth_cookie_name(self) -> str:
        if self.effective_auth_cookie_secure:
            return "__Host-reawote_session"
        return "reawote_dev_session"

    @model_validator(mode="after")
    def validate_security_configuration(self) -> "Settings":
        try:
            ZoneInfo(self.zip_policy_timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("ZIP_POLICY_TIMEZONE must name an installed IANA timezone") from None
        if self.source_mutations_enabled:
            token = self.worker_mutation_token.get_secret_value() if self.worker_mutation_token else ""
            if len(token) < 32 or len(token) > 256 or not token.isascii() or any(not 33 <= ord(char) <= 126 for char in token):
                raise ValueError("Source mutations require a private worker token of 32–256 printable ASCII characters")
        origins = self.parsed_cors_origins
        if not origins:
            raise ValueError("CORS_ORIGINS must contain at least one explicit origin")
        if any(origin == "*" or "*" in origin for origin in origins):
            raise ValueError("Credentialed CORS requires explicit origins; wildcards are forbidden")
        for origin in origins:
            try:
                parsed = urlsplit(origin)
                # Accessing port also validates malformed and out-of-range ports.
                parsed.port
            except ValueError as exc:
                raise ValueError("CORS_ORIGINS entries must be exact HTTP(S) origins") from exc
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or "\\" in origin
                or any(character.isspace() or ord(character) < 32 for character in origin)
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
        if not self.effective_auth_cookie_secure:
            if not self.auth_allow_insecure_cookie:
                raise ValueError("Insecure auth cookies require AUTH_ALLOW_INSECURE_COOKIE=true")
            if self.app_env.casefold() not in {"development", "test", "demo", "e2e"}:
                raise ValueError(
                    "Insecure auth cookies are allowed only in development, test, demo, or e2e"
                )
            if any(
                urlsplit(origin).hostname not in {"localhost", "127.0.0.1", "::1"}
                for origin in origins
            ):
                raise ValueError("Insecure auth cookies require loopback-only CORS_ORIGINS")
        if self.auth_last_seen_update_seconds > self.auth_idle_timeout_minutes * 30:
            raise ValueError(
                "AUTH_LAST_SEEN_UPDATE_SECONDS must not exceed half the idle timeout"
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
