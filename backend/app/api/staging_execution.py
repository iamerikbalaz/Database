"""Explicit upload/reconciliation; neither action publishes an online material."""
from uuid import UUID

from fastapi import APIRouter

from app.api.publication_staging_jobs import StagingClosure
from app.auth.access import AccessDependency
from app.staging_runtime import StagingCoordinator


class StagingAction(StagingClosure):
    expected_last_dispatch_id: UUID | None = None


def build_staging_execution_router(database, worker, inventory, cloud, settings):
    router = APIRouter()
    coordinator = StagingCoordinator(database, worker, inventory, cloud, settings)

    @router.post("/{job_id}/run")
    async def run(job_id: UUID, payload: StagingAction, access: AccessDependency):
        return await coordinator.perform(job_id, payload, access, "EXECUTE")

    @router.post("/{job_id}/reconcile")
    async def reconcile(job_id: UUID, payload: StagingAction, access: AccessDependency):
        return await coordinator.perform(job_id, payload, access, "RECONCILE")

    return router
