from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import HealthDatabase, build_health_router
from app.api.resources import SessionDatabase, build_resources_router
from app.core.config import Settings, get_settings
from app.db.session import Database


class ApplicationDatabase(HealthDatabase, SessionDatabase, Protocol):
    def dispose(self) -> None: ...


def create_app(
    settings: Settings | None = None,
    database: ApplicationDatabase | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    app_database = database or Database(app_settings.resolved_database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        app_database.dispose()

    application = FastAPI(
        title=app_settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.database = app_database
    application.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.parsed_cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["*"],
    )
    application.include_router(build_health_router(app_database))
    application.include_router(build_resources_router(app_database))

    @application.get("/", tags=["system"])
    def root() -> dict[str, str]:
        return {
            "service": "backend",
            "status": "ok",
            "health": "/health",
            "docs": "/docs",
        }

    return application


app = create_app()
