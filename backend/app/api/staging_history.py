"""Bounded staging journal reads and explicit administrative abandonment."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field, field_validator
from sqlalchemy import select

from app.api.material_approvals import PUBLICATION_APPROVERS
from app.api.material_review import _material
from app.api.publication_staging_jobs import (_commit, _conflict, _digest, _job, _request_gate,
    _require_database, _transaction, _view, StagingClosure)
from app.auth.access import ADMIN, AccessDependency
from app.auth.service import _aware
from app.db.models import (MaterialAuditEvent, MaterialPackagingExecution, PublicationStagingJob,
    PublicationStagingItem, PublicationStagingOwner, PublicationStagingClose, PublicationStagingState,
    PublicationStagingDispatch, PublicationStagingTransfer, PublicationStagingObservation, PublicationStagingResult)
from app.staging_dispatch_lease import StagingLeaseError, staging_dispatch_lease


class StagingAbandonment(StagingClosure):
    expected_last_dispatch_id: UUID
    acknowledge_possible_remote_effects: Annotated[bool, Field(strict=True)]

    @field_validator("acknowledge_possible_remote_effects")
    @classmethod
    def acknowledged(cls, value):
        if value is not True:
            raise ValueError("Acknowledge possible late remote effects before abandoning the job.")
        return value


def _exists(session, job_id):
    if session.scalar(select(PublicationStagingJob.id).where(PublicationStagingJob.id == job_id)) is None:
        raise HTTPException(404, "Staging reservation not found.")


def _replay(session, job_id, actor_id, payload, digest):
    prior = session.scalar(select(PublicationStagingClose).where(
        PublicationStagingClose.actor_id == actor_id, PublicationStagingClose.request_key == payload.idempotency_key))
    if prior and (prior.job_id != job_id or prior.request_hash != digest):
        _conflict("GCS_STAGING_REQUEST_KEY_REUSED")
    return prior


def _result(value):
    if value is None: return None
    return dict(id=str(value.id), outcome=value.outcome, failure_code=value.failure_code,
        completion_observation_id=str(value.completion_observation_id) if value.completion_observation_id else None,
        inputs_current=value.inputs_current, actor_current=value.actor_current, lease_current=value.lease_current,
        created_at=_aware(value.created_at).isoformat())


def build_staging_history_router(database, settings):
    router = APIRouter()

    @router.get("/{job_id}/dispatches")
    def dispatches(job_id: UUID, access: AccessDependency,
        after: Annotated[int, Query(ge=0, le=2147483647)] = 0,
        limit: Annotated[int, Query(ge=1, le=50)] = 20):
        with database.session() as session:
            access.check(session, PUBLICATION_APPROVERS); _exists(session, job_id)
            rows = session.execute(select(PublicationStagingDispatch, PublicationStagingResult)
                .outerjoin(PublicationStagingResult, PublicationStagingResult.dispatch_id == PublicationStagingDispatch.id)
                .where(PublicationStagingDispatch.job_id == job_id, PublicationStagingDispatch.ordinal > after)
                .order_by(PublicationStagingDispatch.ordinal).limit(limit + 1)).all()
            return {"items": [dict(id=str(item.id), ordinal=item.ordinal, action=item.action,
                previous_dispatch_id=str(item.previous_dispatch_id) if item.previous_dispatch_id else None,
                actor_id=str(item.actor_id), reason=item.reason, plan_sha256=item.plan_sha256,
                created_at=_aware(item.created_at).isoformat(), result=_result(result)) for item, result in rows[:limit]],
                "next_cursor": rows[limit - 1][0].ordinal if len(rows) > limit else None}

    @router.get("/{job_id}/dispatches/{dispatch_id}/transfers")
    def transfers(job_id: UUID, dispatch_id: UUID, access: AccessDependency,
        after: Annotated[int, Query(ge=0, le=20002)] = 0,
        limit: Annotated[int, Query(ge=1, le=50)] = 20):
        with database.session() as session:
            access.check(session, PUBLICATION_APPROVERS); _exists(session, job_id)
            if session.scalar(select(PublicationStagingDispatch.id).where(
                    PublicationStagingDispatch.id == dispatch_id, PublicationStagingDispatch.job_id == job_id)) is None:
                raise HTTPException(404, "Staging dispatch not found.")
            rows = session.execute(select(PublicationStagingTransfer, PublicationStagingObservation)
                .outerjoin(PublicationStagingObservation, PublicationStagingObservation.transfer_id == PublicationStagingTransfer.id)
                .where(PublicationStagingTransfer.dispatch_id == dispatch_id, PublicationStagingTransfer.ordinal > after)
                .order_by(PublicationStagingTransfer.ordinal).limit(limit + 1)).all()
            return {"items": [dict(id=str(item.id), ordinal=item.ordinal, kind=item.kind,
                relative_path=item.relative_path, size=item.size, sha256=item.sha256,
                created_at=_aware(item.created_at).isoformat(), observation=None if observed is None else dict(
                    id=str(observed.id), outcome=observed.outcome, failure_code=observed.failure_code,
                    receipt=observed.receipt, created_at=_aware(observed.created_at).isoformat())) for item, observed in rows[:limit]],
                "next_cursor": rows[limit - 1][0].ordinal if len(rows) > limit else None}

    @router.post("/{job_id}/abandon")
    def abandon(job_id: UUID, payload: StagingAbandonment, access: AccessDependency):
        digest = _digest("GCS_STAGING_ABANDONED", payload, job_id)
        with database.session() as session:
            actor = access.check(session, ADMIN); _require_database(session, settings); _exists(session, job_id)
            if _replay(session, job_id, actor.id, payload, digest): return _view(session, _job(session, job_id))
        try:
            with staging_dispatch_lease(database.engine, job_id, allow_test_sqlite=settings.app_env == "test") as lease:
                with _transaction(database) as session:
                    actor = access.check(session, ADMIN)
                    _request_gate(session, actor.id, payload.idempotency_key, "close")
                    if _replay(session, job_id, actor.id, payload, digest): return _view(session, _job(session, job_id))
                    items = list(session.scalars(select(PublicationStagingItem).where(PublicationStagingItem.job_id == job_id)
                        .order_by(PublicationStagingItem.material_id)))
                    for item in items: _material(session, item.material_id, access, lock=True)
                    session.scalar(select(PublicationStagingJob.id).where(PublicationStagingJob.id == job_id).with_for_update())
                    job = _job(session, job_id); state = session.get(PublicationStagingState, job_id)
                    if job.plan_sha256 != payload.expected_plan_sha256: _conflict("GCS_STAGING_PLAN_CHANGED")
                    if state is None: _conflict("GCS_STAGING_HISTORY_INVALID")
                    if state.status == "CLOSED": _conflict("GCS_STAGING_ALREADY_CLOSED")
                    if state.last_dispatch_id is None: _conflict("GCS_STAGING_NOT_DISPATCHED")
                    if state.last_dispatch_id != payload.expected_last_dispatch_id: _conflict("GCS_STAGING_PROGRESS_CHANGED")
                    closure = PublicationStagingClose(job_id=job_id, actor_id=actor.id,
                        issuer_session_id=access.context.session.id, request_key=payload.idempotency_key,
                        request_hash=digest, reason=payload.reason, dispatched=True)
                    session.add(closure); session.flush()
                    state.status = "CLOSED"; state.close_id = closure.id
                    for item in items:
                        owner = session.get(PublicationStagingOwner, (job_id, item.material_id))
                        if owner is None or not owner.active: _conflict("GCS_STAGING_OWNERSHIP_INVALID")
                        owner.active = False; owner.close_id = closure.id
                        execution = session.get(MaterialPackagingExecution, item.execution_id)
                        session.add(MaterialAuditEvent(material_id=item.material_id, actor_id=actor.id,
                            event_type="PUBLICATION_STAGING_ABANDONED", generation=execution.input_snapshot["generation"],
                            revision_hash=execution.input_snapshot["revision_hash"], result={"audit": {
                                "job_id": str(job_id), "close_id": str(closure.id), "plan_sha256": job.plan_sha256,
                                "last_dispatch_id": str(state.last_dispatch_id), "reason": payload.reason,
                                "possible_remote_effects_acknowledged": True, "remote_cancellation_confirmed": False}}))
                    access.check(session, ADMIN); lease.require_owned(); _commit(session)
                    return _view(session, job)
        except StagingLeaseError as error:
            raise HTTPException(409 if error.code == "GCS_DISPATCH_BUSY" else 503, {"code": error.code}) from None

    return router
