"""Reserve immutable approved jobs; dispatch is a separate explicit action."""
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import and_, or_, select, func
from sqlalchemy.exc import IntegrityError

from app.api.material_approvals import PUBLICATION_APPROVERS
from app.api.material_review import _material
from app.auth.access import ADMIN, AccessDependency
from app.catalog import Reason
from app.db.models import (MaterialAuditEvent, MaterialPackagingExecution, MaterialPackagingDispatch,
    MaterialPackagingObservation, MaterialPackagingState, PBRMaterial)
from app.packaging_client import PackagingClientError
from app.packaging_contract import PreparedPackaging, PackagingDispatch
from app.packaging_dispatch_lease import PackagingLeaseError, packaging_dispatch_lease
from app.packaging_jobs import (approved_inputs, conflict, execution, preparation, replay, request_hash, summary, validate_prepared, view,
    current_inputs, fresh_source, frozen_report, dispatch_replay, require_progress, validate_dispatched, dispatch_view)
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


def build_packaging_jobs_router(database, worker, settings, inventory_client):
    router = APIRouter(prefix="/api/materials/{material_id}/packaging-executions", tags=["packaging executions"])
    from app.api.packaging_downloads import build_packaging_downloads_router
    router.include_router(build_packaging_downloads_router(database, worker))
    from app.api.packaging_retirement import build_packaging_retirement_router
    router.include_router(build_packaging_retirement_router(database, worker, settings))

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
            access.check(session, PUBLICATION_APPROVERS)
            material = _material(session, material_id, access, historical=True)
            query = select(MaterialPackagingExecution).where(MaterialPackagingExecution.material_id == material_id)
            if after:
                cursor = execution(session, material_id, after)
                stamp = select(MaterialPackagingExecution.created_at).where(MaterialPackagingExecution.id == cursor.id).scalar_subquery()
                query = query.where(or_(MaterialPackagingExecution.created_at < stamp,
                    and_(MaterialPackagingExecution.created_at == stamp, MaterialPackagingExecution.id < cursor.id)))
            rows = list(session.scalars(query.order_by(MaterialPackagingExecution.created_at.desc(), MaterialPackagingExecution.id.desc()).limit(limit + 1)))
            return {"enabled": settings.packaging_enabled, "archived": material.is_archived, "items": [summary(session, row) for row in rows[:limit]],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None}

    @router.get("/{execution_id}")
    def detail(material_id: UUID, execution_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session, PUBLICATION_APPROVERS); _material(session, material_id, access, historical=True)
            return view(session, execution(session, material_id, execution_id))

    @router.get("/{execution_id}/dispatches")
    def dispatch_history(material_id: UUID, execution_id: UUID, access: AccessDependency,
        after: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=50)] = 20):
        with database.session() as session:
            access.check(session, PUBLICATION_APPROVERS); _material(session, material_id, access, historical=True)
            execution(session, material_id, execution_id)
            rows = list(session.scalars(select(MaterialPackagingDispatch).where(
                MaterialPackagingDispatch.execution_id == execution_id, MaterialPackagingDispatch.ordinal > after)
                .order_by(MaterialPackagingDispatch.ordinal).limit(limit + 1)))
            return {"items": [dispatch_view(session, row) for row in rows[:limit]],
                "next_cursor": rows[limit - 1].ordinal if len(rows) > limit else None}

    def observe(item, action, command, prepared, report, access, lease, roles):
        result = None; failure = None; source_current = False; source_failure = None
        try:
            lease.require_owned()
            result = validate_dispatched(worker.dispatch(prepared, report, command), prepared, report, command)
        except (PackagingClientError, PackagingLeaseError) as error:
            failure = error.code
        if result and result.status == "READY" and result.terminal == "OPEN":
            try:
                fresh_source(inventory_client, item); source_current = True
            except HTTPException as error:
                source_failure = error.detail["code"]
        auth_error = None; acceptance_failure = source_failure
        with database.session() as session:
            # Persist factual worker progress even after the original account was
            # revoked. Only a currently authorized account may accept PACKAGED.
            try: access.check(session, roles)
            except HTTPException as error: auth_error = error
            session.scalar(select(PBRMaterial).where(PBRMaterial.id == item.material_id).with_for_update())
            item = execution(session, item.material_id, item.id)
            state = session.get(MaterialPackagingState, item.id)
            inputs_current = False
            if auth_error is None and source_current and state.last_dispatch_id == action.id:
                try:
                    current_inputs(session, item, access); inputs_current = True
                except HTTPException:
                    acceptance_failure = "PACKAGING_APPROVAL_CONTEXT_CHANGED"
            lease_current = True
            try: lease.require_owned()
            except PackagingLeaseError as error:
                lease_current = False; acceptance_failure = error.code
            observed = MaterialPackagingObservation(execution_id=item.id, dispatch_id=action.id,
                outcome=result.status if result else "UNCERTAIN", worker_result=result.model_dump(mode="json") if result else None,
                failure_code=failure, proof_sha256=result.stored.proof_sha256 if result and result.stored else None,
                inputs_current=inputs_current, actor_current=auth_error is None)
            session.add(observed); session.flush()
            # An old response may arrive after a lost lease and a newer command.
            # Its immutable observation cannot replace that command's progress.
            if state.last_dispatch_id == action.id:
                status = "RECOVERY_REQUIRED"
                if lease_current and result:
                    if action.action == "CLOSE" and result.terminal == "CLOSED": status = "REJECTED"
                    elif result.terminal == "OPEN":
                        if result.status == "RETRY_REQUIRED": status = "RETRY_REQUIRED"
                        elif inputs_current and auth_error is None: status = "PACKAGED"
                state.status = status; state.last_observation_id = observed.id
            _audit(session, item, action.actor_id, "PACKAGING_OBSERVED", {"dispatch_id": str(action.id),
                "outcome": observed.outcome, "inputs_current": inputs_current, "actor_current": auth_error is None,
                "acceptance_failure": acceptance_failure, "failure_code": failure})
            _commit(session)
            response = view(session, item)
        if auth_error: raise auth_error
        return response

    def perform(material_id, execution_id, payload, access, action_name):
        roles = ADMIN if action_name == "CLOSE" else PUBLICATION_APPROVERS
        digest = request_hash("PACKAGING_" + action_name + "_" + str(execution_id), material_id, payload)
        with database.session() as session:
            actor = access.check(session, roles); item = execution(session, material_id, execution_id)
            if dispatch_replay(session, item, actor.id, payload.idempotency_key, digest): return view(session, item)
        try:
            with packaging_dispatch_lease(database.engine, execution_id, allow_test_sqlite=settings.app_env == "test") as lease:
                # New conversion reads current source bytes before recording a
                # command. Recovery and closure must remain possible with NAS offline.
                with database.session() as session:
                    actor = access.check(session, roles); _material(session, material_id, access, lock=True)
                    item = execution(session, material_id, execution_id)
                    if dispatch_replay(session, item, actor.id, payload.idempotency_key, digest): return view(session, item)
                    state = require_progress(session, item, payload, action_name)
                    unsent_close = action_name == "CLOSE" and state.status == "RESERVED" and state.last_dispatch_id is None
                    if not unsent_close and not settings.packaging_enabled:
                        raise HTTPException(503, {"code": "PACKAGING_SERVICE_DISABLED"})
                    if action_name in {"EXECUTE", "RETRY"}: current_inputs(session, item, access)
                if action_name in {"EXECUTE", "RETRY"}: fresh_source(inventory_client, item)
                with database.session() as session:
                    actor = access.check(session, roles); _material(session, material_id, access, lock=True)
                    item = execution(session, material_id, execution_id)
                    if dispatch_replay(session, item, actor.id, payload.idempotency_key, digest): return view(session, item)
                    state = require_progress(session, item, payload, action_name)
                    if action_name in {"EXECUTE", "RETRY"}:
                        prepared, report = current_inputs(session, item, access)
                    elif not unsent_close:
                        prepared = PreparedPackaging.model_validate(item.worker_request)
                        report = frozen_report(session, item.input_snapshot)
                    ordinal = (session.scalar(select(func.max(MaterialPackagingDispatch.ordinal)).where(
                        MaterialPackagingDispatch.execution_id == execution_id)) or 0) + 1
                    if ordinal > 2**31 - 1: conflict("PACKAGING_DISPATCH_LIMIT")
                    action = MaterialPackagingDispatch(id=uuid4(), execution_id=execution_id, ordinal=ordinal, actor_id=actor.id,
                        issuer_session_id=access.context.session.id, action=action_name, request_key=payload.idempotency_key,
                        request_hash=digest, reason=payload.reason)
                    session.add(action); session.flush()
                    if unsent_close:
                        observed = MaterialPackagingObservation(execution_id=execution_id, dispatch_id=action.id, outcome="NOT_STARTED",
                            worker_result=None, failure_code=None, proof_sha256=None, inputs_current=False, actor_current=False)
                        session.add(observed); session.flush()
                        state.status = "REJECTED"; state.last_observation_id = observed.id
                    else:
                        state.status = "RUNNING"; state.last_observation_id = None
                    state.last_dispatch_id = action.id
                    _audit(session, item, actor.id, "PACKAGING_CLOSED" if unsent_close else "PACKAGING_DISPATCHED",
                        {"dispatch_id": str(action.id), "action": action_name, "ordinal": ordinal,
                            "outcome": "NOT_STARTED" if unsent_close else None, "reason": payload.reason})
                    lease.require_owned(); _commit(session)
                    if unsent_close: return view(session, item)
                command = PackagingDispatch(id=str(action.id), ordinal=action.ordinal, action=action.action)
                return observe(item, action, command, prepared, report, access, lease, roles)
        except PackagingLeaseError as error:
            raise HTTPException(409 if error.code == "PACKAGING_DISPATCH_BUSY" else 503, {"code": error.code}) from None

    @router.post("/{execution_id}/run")
    def run(material_id: UUID, execution_id: UUID, payload: PackagingAction, access: AccessDependency):
        return perform(material_id, execution_id, payload, access, "EXECUTE")

    @router.post("/{execution_id}/retry")
    def retry(material_id: UUID, execution_id: UUID, payload: PackagingAction, access: AccessDependency):
        return perform(material_id, execution_id, payload, access, "RETRY")

    @router.post("/{execution_id}/reconcile")
    def reconcile(material_id: UUID, execution_id: UUID, payload: PackagingAction, access: AccessDependency):
        return perform(material_id, execution_id, payload, access, "RECONCILE")

    @router.post("/{execution_id}/close")
    def close(material_id: UUID, execution_id: UUID, payload: PackagingAction, access: AccessDependency):
        return perform(material_id, execution_id, payload, access, "CLOSE")

    return router
