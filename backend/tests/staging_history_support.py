"""Synthetic journal facts for persistence tests; never calls cloud or a worker."""
from uuid import uuid4
import json

from sqlalchemy import select

from app.db.models import (PublicationStagingJob, PublicationStagingDispatch, PublicationStagingState,
    PublicationStagingTransfer, PublicationStagingObservation, PublicationStagingResult,
    PublicationStagingClose, PublicationStagingOwner)
from app.gcs_batch import StagingPlan, completion_manifest
from app.gcs_contract import GcsObjectReceipt


def dispatch(session, job_id, *, progress=True, **changes):
    job = session.get(PublicationStagingJob, job_id)
    prior = session.scalar(select(PublicationStagingDispatch).where(PublicationStagingDispatch.job_id == job_id)
        .order_by(PublicationStagingDispatch.ordinal.desc()).limit(1))
    values = dict(job_id=job_id, ordinal=prior.ordinal + 1 if prior else 1,
        previous_dispatch_id=prior.id if prior else None, action="RECONCILE" if prior else "EXECUTE",
        actor_id=job.actor_id, issuer_session_id=job.issuer_session_id, request_key=uuid4(),
        request_hash="a" * 64, plan_sha256=job.plan_sha256, reason="Synthetic journal persistence test")
    item = PublicationStagingDispatch(**(values | changes)); session.add(item); session.flush()
    if progress:
        state = session.get(PublicationStagingState, job_id)
        state.status = "RUNNING"; state.last_dispatch_id = item.id; state.last_result_id = None
        session.flush()
    return item


def transfer(session, dispatched, ordinal=1, **changes):
    job = session.get(PublicationStagingJob, dispatched.job_id)
    plan = StagingPlan.model_validate_json(json.dumps(job.plan))
    if ordinal <= len(plan.body.objects):
        spec = plan.body.objects[ordinal - 1]
        values = dict(kind="DATA", relative_path=spec.relative_path, size=spec.size, sha256=spec.sha256)
    else:
        observed = list(session.scalars(select(PublicationStagingObservation)
            .where(PublicationStagingObservation.dispatch_id == dispatched.id)
            .join(PublicationStagingTransfer, PublicationStagingTransfer.id == PublicationStagingObservation.transfer_id)
            .order_by(PublicationStagingTransfer.ordinal)))
        spec = completion_manifest(plan, tuple(GcsObjectReceipt.model_validate(item.receipt) for item in observed)).specification
        values = dict(kind="MARKER", relative_path=spec.relative_path, size=spec.size, sha256=spec.sha256)
    item = PublicationStagingTransfer(job_id=job.id, dispatch_id=dispatched.id,
        **(dict(ordinal=ordinal) | values | changes))
    session.add(item); session.flush()
    return item


def receipt(session, intent):
    job = session.get(PublicationStagingJob, intent.job_id)
    return dict(spec=dict(job_id=str(job.id), binding_sha256=job.plan_sha256,
        relative_path=intent.relative_path, size=intent.size, sha256=intent.sha256),
        bucket_name=job.bucket_name, object_name=f"{job.staging_prefix}/{job.id}/{intent.relative_path}",
        generation=str(100 + intent.ordinal), metageneration="1")


def observe(session, intent, **changes):
    values = dict(outcome="VERIFIED", receipt=receipt(session, intent), failure_code=None) | changes
    item = PublicationStagingObservation(job_id=intent.job_id, dispatch_id=intent.dispatch_id,
        transfer_id=intent.id, **values)
    session.add(item); session.flush()
    return item


def result(session, dispatched, *, marker=None, progress=True, **changes):
    values = dict(outcome="VERIFIED" if marker else "UNCERTAIN", completion_observation_id=marker.id if marker else None,
        failure_code=None if marker else "GCS_OUTCOME_UNCERTAIN", inputs_current=True, actor_current=True,
        lease_current=True) | changes
    item = PublicationStagingResult(job_id=dispatched.job_id, dispatch_id=dispatched.id, **values)
    session.add(item); session.flush()
    if progress:
        state = session.get(PublicationStagingState, dispatched.job_id)
        state.status = ("STAGED_VERIFIED" if item.outcome == "VERIFIED" and item.inputs_current
            and item.actor_current and item.lease_current else "RECOVERY_REQUIRED")
        state.last_result_id = item.id; session.flush()
    return item


def complete(session, dispatched):
    count = len(session.get(PublicationStagingJob, dispatched.job_id).plan["body"]["objects"])
    for ordinal in range(1, count + 2):
        observed = observe(session, transfer(session, dispatched, ordinal))
    return observed


def close(session, job_id, *, progress=True, **changes):
    job = session.get(PublicationStagingJob, job_id)
    values = dict(job_id=job.id, actor_id=job.actor_id, issuer_session_id=job.issuer_session_id,
        request_key=uuid4(), request_hash="b" * 64, reason="Synthetic closure acknowledging dispatch",
        dispatched=True) | changes
    item = PublicationStagingClose(**values); session.add(item); session.flush()
    if progress:
        state = session.get(PublicationStagingState, job.id)
        state.status = "CLOSED"; state.close_id = item.id
    for owner in session.scalars(select(PublicationStagingOwner).where(PublicationStagingOwner.job_id == job.id)):
        owner.active = False; owner.close_id = item.id
    session.flush()
    return item
