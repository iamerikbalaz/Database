"""Explicit administrator retirement, with committed intent before private IO."""
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.material_approvals import PUBLICATION_APPROVERS
from app.api.material_review import _material
from app.api.packaging_downloads import accepted_snapshot
from app.auth.access import ADMIN, AccessDependency
from app.catalog import Reason
from app.db.models import (PBRMaterial, MaterialPackagingRetirement as Intent,
    MaterialPackagingRetirementDispatch as Dispatch, MaterialPackagingRetirementObservation as Observation)
from app.packaging_client import PackagingClientError
from app.packaging_dispatch_lease import PackagingLeaseError, packaging_dispatch_lease
from app.packaging_jobs import conflict, execution, request_hash
from app.packaging_retirement_jobs import (audit, bound_command, create_intent, dispatch_view, find,
    latest_dispatch, replay, require_staging_released, validate_receipt, verified_removal, view)
from app.schemas import ApiSchema, Sha256


class RetirementRequest(ApiSchema):
    idempotency_key: UUID
    expected_observation_id: UUID
    expected_proof_sha256: Sha256
    acknowledgement: Literal["REMOVE_LOCAL_COPY"]
    reason: Reason


class RetirementRecovery(ApiSchema):
    idempotency_key: UUID
    expected_retirement_id: UUID
    expected_proof_sha256: Sha256
    expected_last_dispatch_id: UUID | None
    acknowledgement: Literal["REMOVE_LOCAL_COPY"]
    reason: Reason


def _commit(session):
    try: session.commit()
    except IntegrityError:
        session.rollback(); conflict("PACKAGING_RETIREMENT_CONCURRENT_CONFLICT")


def build_packaging_retirement_router(database, worker, settings):
    router = APIRouter()

    def enabled(): return settings.packaging_enabled and settings.packaging_retirement_enabled

    def authorize(session, material_id, execution_id, access, *, write=False):
        access.check(session, ADMIN if write else PUBLICATION_APPROVERS)
        # This action removes a historical local copy, without editing the
        # material/source or requiring a current publication approval.
        _material(session, material_id, access, lock=write, historical=True)
        return execution(session, material_id, execution_id)

    @router.get("/{execution_id}/retirement")
    def detail(material_id: UUID, execution_id: UUID, access: AccessDependency):
        with database.session() as session:
            authorize(session, material_id, execution_id, access)
            intent = find(session, material_id, execution_id)
            return {"enabled": enabled(), "retirement": view(session, intent) if intent else None}

    @router.get("/{execution_id}/retirement/dispatches")
    def history(material_id: UUID, execution_id: UUID, access: AccessDependency,
        after: Annotated[int, Query(ge=0, le=2**31 - 1)] = 0, limit: Annotated[int, Query(ge=1, le=50)] = 20):
        with database.session() as session:
            authorize(session, material_id, execution_id, access)
            intent = find(session, material_id, execution_id)
            if intent is None: raise HTTPException(404, {"code": "PACKAGING_RETIREMENT_NOT_FOUND"})
            rows = list(session.scalars(select(Dispatch).where(Dispatch.retirement_id == intent.id, Dispatch.ordinal > after)
                .order_by(Dispatch.ordinal).limit(limit + 1)))
            return {"items": [dispatch_view(session, action) for action in rows[:limit]],
                "next_cursor": rows[limit - 1].ordinal if len(rows) > limit else None}

    def observe(intent, action, context, command, access, lease):
        result = None; failure = None
        try:
            lease.require_owned()
            result = validate_receipt(worker.retire(context.prepared, context.report, context.result, command), context, command)
        except (PackagingClientError, PackagingLeaseError) as error:
            failure = error.code
        auth_error = None
        with database.session() as session:
            try: access.check(session, ADMIN)
            except HTTPException as error: auth_error = error
            session.scalar(select(PBRMaterial).where(PBRMaterial.id == intent.material_id).with_for_update())
            saved = find(session, intent.material_id, intent.execution_id, lock=True)
            lease_current = True
            try: lease.require_owned()
            except PackagingLeaseError: lease_current = False
            # A factual verified removal remains true after revocation or lease
            # loss. Any old UNCERTAIN observation cannot undo an existing receipt.
            observed = Observation(retirement_id=saved.id, dispatch_id=action.id,
                outcome="REMOVED" if result else "UNCERTAIN", receipt=result.model_dump(mode="json") if result else None,
                failure_code=failure, actor_current=auth_error is None, lease_current=lease_current)
            session.add(observed); session.flush()
            audit(session, saved, action.actor_id, "PACKAGING_RETIREMENT_OBSERVED", {"dispatch_id": str(action.id),
                "outcome": observed.outcome, "actor_current": auth_error is None, "lease_current": lease_current, "failure_code": failure})
            _commit(session); output = view(session, saved)
        if auth_error: raise auth_error
        return output

    def perform(material_id, execution_id, payload, access, *, recovery):
        digest = request_hash("PACKAGING_RETIREMENT_" + ("RECOVER_" if recovery else "CREATE_") + str(execution_id), material_id, payload)
        with database.session() as session:
            authorize(session, material_id, execution_id, access, write=True)
            prior = replay(session, access.user.id, material_id, execution_id, payload.idempotency_key, digest, recovery=recovery)
            if prior: return view(session, prior)
        try:
            with packaging_dispatch_lease(database.engine, execution_id, allow_test_sqlite=settings.app_env == "test") as lease:
                with database.session() as session:
                    authorize(session, material_id, execution_id, access, write=True)
                    prior = replay(session, access.user.id, material_id, execution_id, payload.idempotency_key, digest, recovery=recovery)
                    if prior: return view(session, prior)
                    intent = find(session, material_id, execution_id, lock=True)
                    if recovery:
                        if intent is None: raise HTTPException(404, {"code": "PACKAGING_RETIREMENT_NOT_FOUND"})
                        if intent.id != payload.expected_retirement_id or intent.proof_sha256 != payload.expected_proof_sha256:
                            conflict("PACKAGING_RETIREMENT_PROOF_CHANGED")
                        if verified_removal(session, intent): return view(session, intent)
                    elif intent is not None:
                        conflict("PACKAGING_RETIREMENT_ALREADY_REQUESTED")
                    if not enabled(): raise HTTPException(503, {"code": "PACKAGING_RETIREMENT_DISABLED"})
                    context = accepted_snapshot(session, material_id, execution_id, access)
                    require_staging_released(session, execution_id)
                    if intent is None:
                        intent = create_intent(context, payload, access, digest, material_id=material_id)
                        session.add(intent); session.flush()
                        audit(session, intent, access.user.id, "PACKAGING_RETIREMENT_REQUESTED", {
                            "reason": payload.reason, "proof_sha256": intent.proof_sha256,
                            "file_count": intent.file_count, "byte_count": intent.byte_count})
                    command = bound_command(intent, context)
                    previous = latest_dispatch(session, intent)
                    if recovery and (previous.id if previous else None) != payload.expected_last_dispatch_id:
                        conflict("PACKAGING_RETIREMENT_PROGRESS_CHANGED")
                    action = Dispatch(id=uuid4(), retirement_id=intent.id, ordinal=previous.ordinal + 1 if previous else 1,
                        previous_dispatch_id=previous.id if previous else None, action="RECONCILE" if previous else "EXECUTE",
                        actor_id=access.user.id, issuer_session_id=access.context.session.id, request_key=payload.idempotency_key,
                        request_hash=digest, reason=payload.reason, worker_command_hash=intent.worker_command_hash)
                    session.add(action); session.flush()
                    audit(session, intent, access.user.id, "PACKAGING_RETIREMENT_DISPATCHED", {
                        "dispatch_id": str(action.id), "action": action.action, "reason": payload.reason})
                    lease.require_owned(); _commit(session)
                return observe(intent, action, context, command, access, lease)
        except PackagingLeaseError as error:
            raise HTTPException(409, {"code": error.code}) from None

    @router.post("/{execution_id}/retirement")
    def retire(material_id: UUID, execution_id: UUID, payload: RetirementRequest, access: AccessDependency):
        return JSONResponse(perform(material_id, execution_id, payload, access, recovery=False), status_code=201)

    @router.post("/{execution_id}/retirement/reconcile")
    def reconcile(material_id: UUID, execution_id: UUID, payload: RetirementRecovery, access: AccessDependency):
        return perform(material_id, execution_id, payload, access, recovery=True)

    return router
