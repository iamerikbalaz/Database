from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.auth.router import build_auth_router
from app.auth.accounts import build_account_router
from app.auth.validation import safe_request_validation_handler
from app.api.health import HealthDatabase, build_health_router
from app.api.material_operations import build_material_operations_router
from app.api.resources import SessionDatabase, build_resources_router
from app.core.config import Settings, get_settings
from app.db.session import Database
from app.worker_client import MaterialPreflightClient, WorkerClient
from app.inventory_client import InventoryClient, WorkerInventoryClient
from app.api.material_review import build_material_review_router
from app.api.material_approvals import build_material_approvals_router
from app.technical_client import TechnicalClient, WorkerTechnicalClient
from app.identity_client import IdentityClient, WorkerIdentityClient
from app.api.material_identity import build_material_identity_router


class ApplicationDatabase(HealthDatabase, SessionDatabase, Protocol):
    def dispose(self) -> None: ...


def create_app(
    settings: Settings | None = None,
    database: ApplicationDatabase | None = None,
    worker_client: MaterialPreflightClient | None = None,
    inventory_client: InventoryClient | None = None,
    technical_client: TechnicalClient | None = None,
    identity_client: IdentityClient | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    app_database = database or Database(app_settings.resolved_database_url)
    app_worker_client = worker_client or WorkerClient(
        app_settings.worker_base_url,
        app_settings.worker_timeout_seconds,
    )

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
    application.state.settings = app_settings
    application.add_exception_handler(RequestValidationError, safe_request_validation_handler)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.parsed_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Accept", "Content-Type", "X-CSRF-Token"],
    )
    application.include_router(build_auth_router(app_database, app_settings))
    application.include_router(build_account_router(app_database, app_settings))
    application.include_router(build_health_router(app_database))
    application.include_router(build_resources_router(app_database))
    application.include_router(
        build_material_operations_router(app_database, app_worker_client)
    )
    application.include_router(build_material_review_router(app_database,
        inventory_client or WorkerInventoryClient(app_settings.worker_base_url)))
    application.include_router(build_material_approvals_router(app_database,
        technical_client or WorkerTechnicalClient(app_settings.worker_base_url)))
    application.include_router(build_material_identity_router(app_database,
        identity_client or WorkerIdentityClient(app_settings.worker_base_url, app_settings.worker_mutation_token,
                                               app_settings.source_mutations_enabled),
        mutations_enabled=app_settings.source_mutations_enabled))

    @application.middleware("http")
    async def prevent_auth_caching(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

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
