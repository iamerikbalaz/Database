"""Preview internal staging from an explicit complete batch selection; no cloud IO."""
from threading import BoundedSemaphore
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator

from app.auth.access import AccessDependency
from app.publication_staging import prepare_staging
from app.schemas import ApiSchema, Sha256


class StagingPackageSelection(ApiSchema):
    material_id: UUID
    execution_id: UUID
    expected_observation_id: UUID
    expected_proof_sha256: Sha256


class StagingPreview(ApiSchema):
    job_id: UUID
    expected_snapshot_hash: Sha256
    expected_csv_sha256: Sha256
    packages: Annotated[list[StagingPackageSelection], Field(min_length=1, max_length=100)]

    @field_validator("job_id")
    @classmethod
    def random_job(cls, value):
        if value.version != 4:
            raise ValueError("UUIDv4 job identifier required")
        return value

    @field_validator("packages")
    @classmethod
    def unique(cls, values):
        if (len({item.material_id for item in values}) != len(values)
                or len({item.execution_id for item in values}) != len(values)
                or len({item.expected_observation_id for item in values}) != len(values)):
            raise ValueError("Select each material, execution and observation once")
        return sorted(values, key=lambda item: item.material_id)


def build_staging_preview_router(database, settings):
    router = APIRouter(prefix="/api/publication-batches", tags=["publication staging"])
    slot = BoundedSemaphore(1)

    @router.post("/{batch_id}/staging-preview")
    def preview(batch_id: UUID, payload: StagingPreview, access: AccessDependency):
        if not slot.acquire(blocking=False):
            raise HTTPException(503, {"code": "GCS_PREVIEW_BUSY"})
        try:
            with database.session() as session:
                plan = prepare_staging(session, batch_id, payload, access, settings)
            return JSONResponse(content={"job_id": plan.body.job_id, "batch_id": plan.body.batch_id,
                "plan_sha256": plan.sha256, "layout": plan.body.layout,
                "transfer_enabled": settings.gcs_enabled, "importer_compatible": False,
                "bucket_name": plan.body.bucket_name, "staging_prefix": plan.body.staging_prefix,
                "object_count": len(plan.body.objects), "total_bytes": sum(item.size for item in plan.body.objects),
                "materials": [item.model_dump(mode="json") for item in plan.body.materials],
                "objects_preview": [item.model_dump(mode="json") for item in plan.body.objects[:20]],
                "has_more_objects": len(plan.body.objects) > 20},
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        finally:
            slot.release()

    return router
