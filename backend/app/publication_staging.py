"""Read-only compilation from current approved, server-owned publication records."""
from dataclasses import dataclass
import json
from types import SimpleNamespace
from fastapi import HTTPException
from sqlalchemy import Text, cast, func, select

from app.api.material_approvals import PUBLICATION_APPROVERS
from app.api.material_review import _material
from app.api.packaging_downloads import accepted
from app.db.models import (MaterialPackagingExecution, MaterialPackagingObservation,
                           PublicationBatch, PublicationBatchItem, PublicationStagingJob,
                           PublicationStagingItem, PublicationStagingClose)
from app.gcs_batch import GcsBatchError, PackageInput, StagingPlan, compile_staging_plan
from app.gcs_contract import GcsConfiguration
from app.material_review import canonical_hash
from app.packaging_jobs import current_inputs
from app.publication_csv import PublicationCsvRow

MAX_PROOF_TEXT = 64 * 1024**2


@dataclass(frozen=True)
class PreparedStaging:
    plan: StagingPlan
    csv_bytes: bytes
    packages: tuple[PackageInput, ...]


def _require(condition, code="GCS_BATCH_INPUTS_CHANGED"):
    if not condition:
        raise HTTPException(409, {"code": code})


def prepare_staging(session, batch_id, payload, access, settings):
    # A public preview/reservation never borrows another job's material ownership,
    # even when its client-supplied proposed UUID happens to match that job.
    return _prepare(session, batch_id, payload, access, settings).plan


def _prepare(session, batch_id, payload, access, settings, *, staging_job_id=None):
    access.check(session, PUBLICATION_APPROVERS)
    batch = session.get(PublicationBatch, batch_id)
    if batch is None:
        raise HTTPException(404, "Publication batch not found.")
    _require(batch.snapshot_hash == payload.expected_snapshot_hash
             and batch.csv_sha256 == payload.expected_csv_sha256)
    items = list(session.scalars(select(PublicationBatchItem).where(
        PublicationBatchItem.batch_id == batch_id).order_by(PublicationBatchItem.material_id)))
    selection = {item.material_id: item for item in payload.packages}
    _require(len(items) == batch.row_count == len(selection)
             and set(selection) == {item.material_id for item in items}, "GCS_BATCH_SELECTION_INCOMPLETE")
    # Acquire every material before the global folder/brand locks used below.
    # Otherwise a multi-material preview could invert single-material lock order.
    for item in items:
        _material(session, item.material_id, access, lock=True)
    try:
        configuration = GcsConfiguration(enabled=True, bucket_name=settings.gcs_bucket_name,
            staging_prefix=settings.gcs_staging_prefix)
    except ValueError:
        raise HTTPException(503, {"code": "GCS_TARGET_NOT_CONFIGURED"}) from None

    # Bound proof loading before ORM JSON decoding, even for a 100-material batch.
    proof_size = session.scalar(select(func.sum(func.length(cast(MaterialPackagingObservation.worker_result, Text)))).where(
        MaterialPackagingObservation.id.in_([item.expected_observation_id for item in payload.packages]),
        MaterialPackagingObservation.execution_id.in_([item.execution_id for item in payload.packages]))) or 0
    if proof_size > MAX_PROOF_TEXT:
        raise HTTPException(413, {"code": "GCS_BATCH_PROOFS_TOO_LARGE"})
    _require(canonical_hash({"schema_version": 1, "snapshots": [item.snapshot for item in items]}) == batch.snapshot_hash)
    packages = []
    rows = []
    try:
        for item in items:
            selected = selection[item.material_id]
            _require(canonical_hash(item.snapshot) == item.snapshot_hash)
            context = accepted(session, item.material_id, selected.execution_id, access)
            _require(context.observation_id == selected.expected_observation_id
                     and context.result.stored.proof_sha256 == selected.expected_proof_sha256,
                     "GCS_PACKAGING_PROOF_CHANGED")
            execution = session.get(MaterialPackagingExecution, selected.execution_id)
            _require(execution.batch_id == batch_id and execution.input_hash == item.snapshot_hash,
                     "GCS_PACKAGING_BATCH_MISMATCH")
            prepared, report = current_inputs(session, execution, access, staging_job_id=staging_job_id)
            _require(prepared == context.prepared and report == context.report)
            row = PublicationCsvRow.model_validate(item.snapshot["csv_row"])
            _require(row.material_id == item.material_id and row.revision_hash == item.revision_hash
                     and row.content_context_hash == item.content_context_hash)
            rows.append(row)
            packages.append(PackageInput(item.material_id, item.snapshot_hash, prepared, report, context.result))
        plan = compile_staging_plan(job_id=payload.job_id, batch_id=batch_id,
            batch_snapshot_sha256=batch.snapshot_hash, csv_bytes=batch.csv_bytes,
            csv_sha256=batch.csv_sha256, rows=tuple(rows), packages=tuple(packages), configuration=configuration)
    except (GcsBatchError, ValueError, TypeError, KeyError, AttributeError, ArithmeticError, RecursionError):
        raise HTTPException(503, {"code": "GCS_BATCH_PROOF_INVALID"}) from None
    access.check(session, PUBLICATION_APPROVERS)
    return PreparedStaging(plan, batch.csv_bytes, tuple(packages))


def reserved_staging(session, job_id, access, settings):
    """Rebuild an active reservation from durable facts for a future dispatch.

    This performs current auth and approval/ownership checks, but no live source
    check, external IO or lease acquisition. The runner must supply those steps.
    """
    access.check(session, PUBLICATION_APPROVERS)
    job = session.get(PublicationStagingJob, job_id)
    if job is None:
        raise HTTPException(404, "Staging reservation not found.")
    _require(not session.scalar(select(PublicationStagingClose.id).where(
        PublicationStagingClose.job_id == job.id)), "GCS_STAGING_ALREADY_CLOSED")
    try:
        plan = StagingPlan.model_validate_json(json.dumps(job.plan))
        if (plan.sha256 != job.plan_sha256 or plan.body.job_id != str(job.id)
                or plan.body.batch_id != str(job.batch_id) or plan.body.bucket_name != job.bucket_name
                or plan.body.staging_prefix != job.staging_prefix or len(plan.body.materials) != job.material_count):
            raise GcsBatchError()
        csv = next(item for item in plan.body.objects if item.material_id is None)
    except (GcsBatchError, ValueError, TypeError, KeyError, AttributeError, RecursionError, StopIteration):
        raise HTTPException(503, {"code": "GCS_STAGING_HISTORY_INVALID"}) from None
    items = list(session.scalars(select(PublicationStagingItem).where(PublicationStagingItem.job_id == job.id)
        .order_by(PublicationStagingItem.material_id)))
    _require(len(items) == job.material_count, "GCS_STAGING_OWNERSHIP_CHANGED")
    payload = SimpleNamespace(job_id=job.id, expected_snapshot_hash=plan.body.batch_snapshot_sha256,
        expected_csv_sha256=csv.sha256, packages=[SimpleNamespace(material_id=item.material_id,
            execution_id=item.execution_id, expected_observation_id=item.observation_id,
            expected_proof_sha256=item.packaging_proof_sha256) for item in items])
    prepared = _prepare(session, job.batch_id, payload, access, settings, staging_job_id=job.id)
    _require(prepared.plan == plan, "GCS_STAGING_INPUTS_CHANGED")
    return prepared
