"""Durable internal staging reservations and closure before any cloud dispatch."""
from contextlib import contextmanager
import hashlib
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import and_, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer

from app.api.material_approvals import PUBLICATION_APPROVERS
from app.api.material_review import _material
from app.api.publication_staging import StagingPreview
from app.auth.access import AccessDependency
from app.auth.service import _aware
from app.catalog import Reason
from app.db.models import (MaterialAuditEvent, MaterialPackagingExecution, PublicationStagingJob,
    PublicationStagingItem, PublicationStagingOwner, PublicationStagingClose, PublicationStagingState,
    PublicationStagingDispatch)
from app.gcs_batch import StagingPlan, GcsBatchError
from app.material_review import canonical_hash
from app.publication_staging import prepare_staging
from app.schemas import ApiSchema, Sha256


class StagingReservation(StagingPreview):
    batch_id: UUID
    idempotency_key: UUID
    expected_plan_sha256: Sha256
    reason: Reason


class StagingClosure(ApiSchema):
    idempotency_key: UUID
    expected_plan_sha256: Sha256
    reason: Reason


def _conflict(code):
    raise HTTPException(409, {"code": code})


def _digest(operation, payload, job_id):
    return canonical_hash({"operation": operation, "job_id": str(job_id), "payload": payload.model_dump(mode="json")})


def _commit(session):
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        _conflict("GCS_STAGING_CONCURRENT_CONFLICT")


@contextmanager
def _transaction(database):
    with database.session() as session:
        try:
            yield session
        except IntegrityError:
            session.rollback()
            _conflict("GCS_STAGING_CONCURRENT_CONFLICT")


def _request_gate(session, actor_id, request_key, action):
    if session.get_bind().dialect.name == "postgresql":
        value = hashlib.sha256(f"reawote/staging-request/v1/{action}/{actor_id}/{request_key}".encode()).digest()[:8]
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int.from_bytes(value, "big", signed=True)})


def _require_database(session, settings):
    dialect = session.get_bind().dialect.name
    if dialect != "postgresql" and not (dialect == "sqlite" and settings.app_env == "test"):
        raise HTTPException(503, {"code": "GCS_STAGING_DATABASE_UNSUPPORTED"})


def _job(session, identifier):
    job = session.get(PublicationStagingJob, identifier)
    if job is None:
        raise HTTPException(404, "Staging reservation not found.")
    return job


def _summary(session, job):
    closed = session.scalar(select(PublicationStagingClose).where(PublicationStagingClose.job_id == job.id))
    state = session.get(PublicationStagingState, job.id)
    if state is None:
        raise HTTPException(503, {"code": "GCS_STAGING_HISTORY_INVALID"})
    return {"id": str(job.id), "batch_id": str(job.batch_id), "actor_id": str(job.actor_id),
        "status": state.status, "plan_sha256": job.plan_sha256,
        "last_dispatch_id": str(state.last_dispatch_id) if state.last_dispatch_id else None,
        "last_result_id": str(state.last_result_id) if state.last_result_id else None,
        "material_count": job.material_count, "bucket_name": job.bucket_name,
        "staging_prefix": job.staging_prefix, "created_at": _aware(job.created_at).isoformat(),
        "close": None if closed is None else {"id": str(closed.id), "actor_id": str(closed.actor_id),
            "reason": closed.reason, "dispatched": closed.dispatched,
            "created_at": _aware(closed.created_at).isoformat()}}


def _view(session, job):
    try:
        plan = StagingPlan.model_validate_json(json.dumps(job.plan))
        if plan.sha256 != job.plan_sha256 or plan.body.job_id != str(job.id):
            raise GcsBatchError()
    except (GcsBatchError, ValueError, TypeError, KeyError, RecursionError):
        raise HTTPException(503, {"code": "GCS_STAGING_HISTORY_INVALID"}) from None
    return {**_summary(session, job), "reason": job.reason, "layout": plan.body.layout,
        "importer_compatible": False, "object_count": len(plan.body.objects),
        "total_bytes": sum(item.size for item in plan.body.objects),
        "materials": [item.model_dump(mode="json") for item in plan.body.materials]}


def build_staging_jobs_router(database, settings):
    router = APIRouter(prefix="/api/publication-staging-jobs", tags=["publication staging"])

    @router.post("")
    def reserve(payload: StagingReservation, access: AccessDependency):
        batch_id = payload.batch_id
        digest = canonical_hash({"batch_id": str(batch_id), "request": _digest("GCS_STAGING_RESERVED", payload, payload.job_id)})
        with _transaction(database) as session:
            actor = access.check(session, PUBLICATION_APPROVERS)
            _require_database(session, settings)
            _request_gate(session, actor.id, payload.idempotency_key, "reserve")
            prior = session.scalar(select(PublicationStagingJob).where(
                PublicationStagingJob.actor_id == actor.id, PublicationStagingJob.request_key == payload.idempotency_key))
            if prior:
                if prior.request_hash != digest:
                    _conflict("GCS_STAGING_REQUEST_KEY_REUSED")
                return JSONResponse(status_code=201, content=_view(session, prior))
            plan = prepare_staging(session, batch_id, payload, access, settings)
            if plan.sha256 != payload.expected_plan_sha256:
                _conflict("GCS_STAGING_PLAN_CHANGED")
            if session.get(PublicationStagingJob, payload.job_id) is not None:
                _conflict("GCS_STAGING_JOB_ID_REUSED")
            job = PublicationStagingJob(id=payload.job_id, batch_id=batch_id, actor_id=actor.id,
                issuer_session_id=access.context.session.id, request_key=payload.idempotency_key, request_hash=digest,
                reason=payload.reason, material_count=len(plan.body.materials), bucket_name=plan.body.bucket_name,
                staging_prefix=plan.body.staging_prefix, plan_sha256=plan.sha256, plan=plan.model_dump(mode="json"))
            session.add(job); session.flush()
            session.add(PublicationStagingState(job_id=job.id, status="RESERVED"))
            selected = {item.material_id: item for item in payload.packages}
            for bound in plan.body.materials:
                material_id = UUID(bound.material_id)
                execution = session.get(MaterialPackagingExecution, selected[material_id].execution_id)
                item = PublicationStagingItem(job_id=job.id, material_id=material_id, batch_id=batch_id,
                    execution_id=execution.id, observation_id=selected[material_id].expected_observation_id,
                    brand_id=execution.brand_id, folder_path=execution.folder_path, input_hash=execution.input_hash,
                    worker_request_hash=execution.worker_request_hash, packaging_proof_sha256=bound.packaging_proof_sha256)
                session.add(item); session.flush()
                session.add(PublicationStagingOwner(job_id=job.id, material_id=material_id, active=True))
                session.add(MaterialAuditEvent(material_id=material_id, actor_id=actor.id,
                    event_type="PUBLICATION_STAGING_RESERVED", generation=execution.input_snapshot["generation"],
                    revision_hash=execution.input_snapshot["revision_hash"], result={"audit": {"job_id": str(job.id),
                        "batch_id": str(batch_id), "plan_sha256": job.plan_sha256, "reason": payload.reason}}))
            access.check(session, PUBLICATION_APPROVERS)
            _commit(session)
            return JSONResponse(status_code=201, content=_view(session, job))

    @router.get("")
    def history(access: AccessDependency, after: UUID | None = None, limit: Annotated[int, Query(ge=1, le=50)] = 20):
        with database.session() as session:
            access.check(session, PUBLICATION_APPROVERS)
            query = select(PublicationStagingJob).options(defer(PublicationStagingJob.plan))
            if after:
                cursor = _job(session, after)
                stamp = select(PublicationStagingJob.created_at).where(PublicationStagingJob.id == after).scalar_subquery()
                query = query.where(or_(PublicationStagingJob.created_at < stamp,
                    and_(PublicationStagingJob.created_at == stamp, PublicationStagingJob.id < cursor.id)))
            rows = list(session.scalars(query.order_by(PublicationStagingJob.created_at.desc(), PublicationStagingJob.id.desc()).limit(limit + 1)))
            return {"enabled": settings.gcs_enabled, "items": [_summary(session, item) for item in rows[:limit]],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None}

    @router.get("/{job_id}")
    def detail(job_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session, PUBLICATION_APPROVERS)
            return _view(session, _job(session, job_id))

    @router.post("/{job_id}/close")
    def close(job_id: UUID, payload: StagingClosure, access: AccessDependency):
        digest = _digest("GCS_STAGING_CLOSED_UNSENT", payload, job_id)
        with _transaction(database) as session:
            actor = access.check(session, PUBLICATION_APPROVERS)
            _require_database(session, settings)
            _request_gate(session, actor.id, payload.idempotency_key, "close")
            prior = session.scalar(select(PublicationStagingClose).where(PublicationStagingClose.actor_id == actor.id,
                PublicationStagingClose.request_key == payload.idempotency_key))
            if prior:
                if prior.job_id != job_id or prior.request_hash != digest:
                    _conflict("GCS_STAGING_REQUEST_KEY_REUSED")
                return _view(session, _job(session, job_id))
            job = _job(session, job_id)
            if job.plan_sha256 != payload.expected_plan_sha256:
                _conflict("GCS_STAGING_PLAN_CHANGED")
            items = list(session.scalars(select(PublicationStagingItem).where(PublicationStagingItem.job_id == job.id)
                .order_by(PublicationStagingItem.material_id)))
            for item in items:
                _material(session, item.material_id, access, lock=True)
            session.scalar(select(PublicationStagingJob.id).where(PublicationStagingJob.id == job.id).with_for_update())
            if session.scalar(select(PublicationStagingClose.id).where(PublicationStagingClose.job_id == job.id)):
                _conflict("GCS_STAGING_ALREADY_CLOSED")
            if session.scalar(select(PublicationStagingDispatch.id).where(PublicationStagingDispatch.job_id == job.id).limit(1)):
                _conflict("GCS_STAGING_ALREADY_DISPATCHED")
            closure = PublicationStagingClose(job_id=job.id, actor_id=actor.id, issuer_session_id=access.context.session.id,
                request_key=payload.idempotency_key, request_hash=digest, reason=payload.reason)
            session.add(closure); session.flush()
            state = session.get(PublicationStagingState, job.id)
            if state is None:
                _conflict("GCS_STAGING_HISTORY_INVALID")
            state.status = "CLOSED"; state.close_id = closure.id
            for item in items:
                owner = session.get(PublicationStagingOwner, (job.id, item.material_id))
                if owner is None or not owner.active:
                    _conflict("GCS_STAGING_OWNERSHIP_INVALID")
                owner.active = False; owner.close_id = closure.id
                execution = session.get(MaterialPackagingExecution, item.execution_id)
                session.add(MaterialAuditEvent(material_id=item.material_id, actor_id=actor.id,
                    event_type="PUBLICATION_STAGING_CLOSED", generation=execution.input_snapshot["generation"],
                    revision_hash=execution.input_snapshot["revision_hash"], result={"audit": {"job_id": str(job.id),
                        "close_id": str(closure.id), "plan_sha256": job.plan_sha256, "reason": payload.reason,
                        "closed_before_dispatch": True}}))
            access.check(session, PUBLICATION_APPROVERS)
            _commit(session)
            return _view(session, job)

    return router
