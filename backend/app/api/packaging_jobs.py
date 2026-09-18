"""Reserve immutable approved jobs; dispatch is a separate explicit action."""
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError

from app.api.material_approvals import PUBLICATION_APPROVERS
from app.api.material_review import _material
from app.auth.access import ADMIN, AccessDependency
from app.catalog import Reason
from app.db.models import (MaterialAuditEvent, MaterialPackagingExecution, MaterialPackagingDispatch,
    MaterialPackagingObservation, MaterialPackagingState)
from app.packaging_client import PackagingClientError
from app.packaging_dispatch_lease import PackagingLeaseError, packaging_dispatch_lease
from app.packaging_jobs import approved_inputs, conflict, execution, preparation, replay, request_hash, summary, validate_prepared, view
from app.schemas import ApiSchema, Sha256


class PackagingReservation(ApiSchema):
    idempotency_key: UUID
    batch_id: UUID
    expected_snapshot_hash: Sha256
    expected_policy_id: UUID
    reason: Reason


class PackagingAction(ApiSchema):
    idempotency_key: UUID
    expected_last_dispatch_id: UUID | None = None
    reason: Reason


def _audit(session, item, actor_id, event, details):
    session.add(MaterialAuditEvent(material_id=item.material_id, actor_id=actor_id, event_type=event,
        generation=item.input_snapshot["generation"], revision_hash=item.input_snapshot["revision_hash"],
        result={"audit": {"execution_id": str(item.id), **details}}))


def _commit(session):
    try: session.commit()
    except IntegrityError:
        session.rollback()
        conflict("PACKAGING_CONCURRENT_CONFLICT")


def build_packaging_jobs_router(database, worker, settings):
    router = APIRouter(prefix="/api/materials/{material_id}/packaging-executions", tags=["packaging executions"])

    @router.post("")
    def reserve(material_id: UUID, payload: PackagingReservation, access: AccessDependency):
        digest = request_hash("PACKAGING_RESERVED", material_id, payload)
        operation_id = uuid4()
        with database.session() as session:
            actor = access.check(session, PUBLICATION_APPROVERS)
            previous = replay(session, actor.id, material_id, payload.idempotency_key, digest)
            if previous: return JSONResponse(status_code=201, content=view(session, previous))
            if not settings.packaging_enabled: raise HTTPException(503, {"code": "PACKAGING_SERVICE_DISABLED"})
            material, item, policy, report, approval_hash = approved_inputs(session, material_id, payload, access)
            inputs = preparation(operation_id, material, item, policy, report, approval_hash)
        # Pure plan preparation has no filesystem side effects and occurs outside
        # the transaction. Nothing has been reserved or dispatched at this point.
        try: prepared = validate_prepared(worker.prepare(inputs), inputs)
        except PackagingClientError as error: raise HTTPException(503, {"code": error.code}) from None
        with database.session() as session:
            actor = access.check(session, PUBLICATION_APPROVERS)
            previous = replay(session, actor.id, material_id, payload.idempotency_key, digest)
            if previous: return JSONResponse(status_code=201, content=view(session, previous))
            material, item, policy, report, approval_hash = approved_inputs(session, material_id, payload, access)
            current = preparation(operation_id, material, item, policy, report, approval_hash)
            if current != inputs: conflict("PACKAGING_APPROVAL_CONTEXT_CHANGED")
            validate_prepared(prepared, current)
            record = MaterialPackagingExecution(id=operation_id, material_id=material_id, batch_id=item.batch_id,
                brand_id=material.published_brand_id, policy_id=policy.id, actor_id=actor.id,
                issuer_session_id=access.context.session.id, request_key=payload.idempotency_key, request_hash=digest,
                reason=payload.reason, folder_path=material.folder_path, input_snapshot=item.snapshot, input_hash=item.snapshot_hash,
                worker_request=prepared.model_dump(mode="json"), worker_request_hash=prepared.request_hash)
            session.add(record); session.flush()
            session.add(MaterialPackagingState(execution_id=record.id, material_id=material_id, status="RESERVED"))
            _audit(session, record, actor.id, "PACKAGING_RESERVED", {"batch_id": str(item.batch_id),
                "policy_id": str(policy.id), "input_hash": item.snapshot_hash, "reason": payload.reason})
            _commit(session)
            return JSONResponse(status_code=201, content=view(session, record))

    @router.get("")
    def history(material_id: UUID, access: AccessDependency, after: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20):
        with database.session() as session:
            access.check(session, PUBLICATION_APPROVERS); _material(session, material_id, access)
            query = select(MaterialPackagingExecution).where(MaterialPackagingExecution.material_id == material_id)
            if after:
                cursor = execution(session, material_id, after)
                stamp = select(MaterialPackagingExecution.created_at).where(MaterialPackagingExecution.id == cursor.id).scalar_subquery()
                query = query.where(or_(MaterialPackagingExecution.created_at < stamp,
                    and_(MaterialPackagingExecution.created_at == stamp, MaterialPackagingExecution.id < cursor.id)))
            rows = list(session.scalars(query.order_by(MaterialPackagingExecution.created_at.desc(), MaterialPackagingExecution.id.desc()).limit(limit + 1)))
            return {"enabled": settings.packaging_enabled, "items": [summary(session, row) for row in rows[:limit]],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None}

    @router.get("/{execution_id}")
    def detail(material_id: UUID, execution_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session, PUBLICATION_APPROVERS); _material(session, material_id, access)
            return view(session, execution(session, material_id, execution_id))

    @router.post("/{execution_id}/close")
    def close(material_id: UUID, execution_id: UUID, payload: PackagingAction, access: AccessDependency):
        digest = request_hash("PACKAGING_CLOSE_" + str(execution_id), material_id, payload)
        with database.session() as session:
            access.check(session, ADMIN); execution(session, material_id, execution_id)
        try:
            with packaging_dispatch_lease(database.engine, execution_id, allow_test_sqlite=settings.app_env == "test") as lease:
                with database.session() as session:
                    actor = access.check(session, ADMIN); _material(session, material_id, access, lock=True)
                    item = execution(session, material_id, execution_id)
                    prior = session.scalar(select(MaterialPackagingDispatch).where(MaterialPackagingDispatch.actor_id == actor.id,
                        MaterialPackagingDispatch.request_key == payload.idempotency_key))
                    if prior:
                        if prior.execution_id != execution_id or prior.request_hash != digest: conflict("PACKAGING_REQUEST_KEY_REUSED")
                        return view(session, item)
                    state = session.get(MaterialPackagingState, execution_id)
                    if state.last_dispatch_id != payload.expected_last_dispatch_id: conflict("PACKAGING_PROGRESS_CHANGED")
                    if state.status != "RESERVED" or state.last_dispatch_id is not None:
                        conflict("PACKAGING_RECONCILIATION_REQUIRED")
                    action = MaterialPackagingDispatch(execution_id=execution_id, ordinal=1, actor_id=actor.id,
                        issuer_session_id=access.context.session.id, action="CLOSE", request_key=payload.idempotency_key,
                        request_hash=digest, reason=payload.reason)
                    session.add(action); session.flush()
                    observed = MaterialPackagingObservation(execution_id=execution_id, dispatch_id=action.id, outcome="NOT_STARTED",
                        worker_result=None, failure_code=None, proof_sha256=None, inputs_current=False, actor_current=False)
                    session.add(observed); session.flush()
                    state.status = "REJECTED"; state.last_dispatch_id = action.id; state.last_observation_id = observed.id
                    _audit(session, item, actor.id, "PACKAGING_CLOSED", {"dispatch_id": str(action.id), "outcome": "NOT_STARTED", "reason": payload.reason})
                    lease.require_owned(); _commit(session)
                    return view(session, item)
        except PackagingLeaseError as error:
            raise HTTPException(409 if error.code == "PACKAGING_DISPATCH_BUSY" else 503, {"code": error.code}) from None

    return router
