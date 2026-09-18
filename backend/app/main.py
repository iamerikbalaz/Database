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
from app.api.catalog import build_catalog_router
from app.api.content_approvals import build_content_approvals_router
from app.api.material_previews import build_material_previews_router
from app.preview_client import PreviewClient, WorkerPreviewClient
from app.api.material_imports import build_material_imports_router
from app.api.folder_discovery import build_folder_discovery_router
from app.discovery_client import DiscoveryClient, WorkerDiscoveryClient
from app.api.ai_content import build_ai_content_router
from app.api.ai_service import build_ai_service_router
from app.publication_preflight import build_publication_preview_router
from app.api.publication_batches import build_publication_batches_router
from app.api.packaging_policy import build_packaging_policy_router
from app.api.packaging_jobs import build_packaging_jobs_router
from app.packaging_client import PackagingClient, WorkerPackagingClient
from app.api.publication_staging import build_staging_preview_router
from app.api.publication_staging_jobs import build_staging_jobs_router
from app.api.staging_history import build_staging_history_router
from app.api.staging_execution import build_staging_execution_router
from app.gcs_client import GcsClient


class ApplicationDatabase(HealthDatabase, SessionDatabase, Protocol):
    def dispose(self) -> None: ...


def create_app(
    settings: Settings | None = None,
    database: ApplicationDatabase | None = None,
    worker_client: MaterialPreflightClient | None = None,
    inventory_client: InventoryClient | None = None,
    technical_client: TechnicalClient | None = None,
    identity_client: IdentityClient | None = None,
    preview_client: PreviewClient | None = None,
    discovery_client: DiscoveryClient | None = None,
    packaging_client: PackagingClient | None = None,
    gcs_client: GcsClient | None = None,
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
    application.include_router(build_catalog_router(app_database))
    application.include_router(build_material_imports_router(app_database))
    application.include_router(build_ai_content_router(app_database))
    application.include_router(build_ai_service_router(app_database))
    application.include_router(build_publication_preview_router(app_database))
    application.include_router(build_publication_batches_router(app_database))
    application.include_router(build_staging_preview_router(app_database, app_settings))
    application.include_router(build_staging_jobs_router(app_database, app_settings))
    application.include_router(build_staging_history_router(app_database, app_settings),
        prefix="/api/publication-staging-jobs", tags=["publication staging"])
    application.include_router(build_packaging_policy_router(app_database, app_settings))
    app_packaging_client = packaging_client or WorkerPackagingClient(app_settings.packaging_base_url,
        token=app_settings.packaging_service_token, enabled=app_settings.packaging_enabled,
        timeout_seconds=app_settings.packaging_timeout_seconds)
    app_inventory_client = inventory_client or WorkerInventoryClient(app_settings.worker_base_url)
    application.include_router(build_packaging_jobs_router(app_database, app_packaging_client, app_settings, app_inventory_client))
    application.include_router(build_staging_execution_router(app_database, app_packaging_client,
        app_inventory_client, gcs_client or GcsClient.from_settings(app_settings), app_settings),
        prefix="/api/publication-staging-jobs", tags=["publication staging"])
    application.include_router(build_folder_discovery_router(app_database,
        discovery_client or WorkerDiscoveryClient(app_settings.worker_base_url)))
    application.include_router(build_content_approvals_router(app_database))
    application.include_router(build_material_previews_router(app_database,
        preview_client or WorkerPreviewClient(app_settings.worker_base_url)))
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
