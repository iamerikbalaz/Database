"""Immutable retirement bindings and monotonic views of local-copy removal."""
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.auth.service import _aware
from app.db.models import (MaterialAuditEvent, MaterialPackagingExecution,
    MaterialPackagingRetirement as Intent, MaterialPackagingRetirementDispatch as Dispatch,
    MaterialPackagingRetirementObservation as Observation, PublicationStagingItem, PublicationStagingOwner)
from app.material_review import canonical_hash
from app.packaging_client import PackagingClientError
from app.packaging_jobs import conflict
from app.packaging_retirement_contract import PackagingRetirementCommand, PackagingRetirementReceipt


def find(session, material_id, execution_id, *, lock=False):
    query = select(Intent).where(Intent.execution_id == execution_id, Intent.material_id == material_id)
    return session.scalar(query.with_for_update() if lock else query)


def require_available(session, execution_id):
    if session.scalar(select(Intent.id).where(Intent.execution_id == execution_id).limit(1)):
        conflict("PACKAGING_COPY_RETIRING")


def require_staging_released(session, execution_id):
    query = select(PublicationStagingOwner.job_id).join(PublicationStagingItem,
        (PublicationStagingItem.job_id == PublicationStagingOwner.job_id)
        & (PublicationStagingItem.material_id == PublicationStagingOwner.material_id)).where(
            PublicationStagingItem.execution_id == execution_id, PublicationStagingOwner.active.is_(True)).limit(1)
    if session.scalar(query): conflict("PACKAGING_RETIREMENT_STAGING_ACTIVE")


def create_intent(context, payload, access, digest, *, material_id):
    identifier = uuid4(); stored = context.result.stored
    if context.observation_id != payload.expected_observation_id or stored.proof_sha256 != payload.expected_proof_sha256:
        conflict("PACKAGING_RETIREMENT_PROOF_CHANGED")
    action = PackagingRetirementCommand(retirement_id=str(identifier), proof_sha256=stored.proof_sha256)
    return Intent(id=identifier, material_id=material_id, execution_id=context.execution_id,
        accepted_observation_id=context.observation_id, actor_id=access.user.id, issuer_session_id=access.context.session.id,
        request_key=payload.idempotency_key, request_hash=digest, reason=payload.reason,
        worker_request_hash=context.prepared.request_hash, plan_hash=context.prepared.request.plan_hash,
        proof_sha256=stored.proof_sha256, worker_command_hash=canonical_hash(action.document(context.prepared)),
        file_count=len(stored.payload.files), byte_count=sum(item.size for item in stored.payload.files))


def bound_command(intent, context):
    action = PackagingRetirementCommand(retirement_id=str(intent.id), proof_sha256=intent.proof_sha256)
    stored = context.result.stored
    if (context.execution_id != intent.execution_id or context.observation_id != intent.accepted_observation_id
            or context.prepared.request_hash != intent.worker_request_hash or context.prepared.request.plan_hash != intent.plan_hash
            or stored.proof_sha256 != intent.proof_sha256 or canonical_hash(action.document(context.prepared)) != intent.worker_command_hash
            or len(stored.payload.files) != intent.file_count or sum(item.size for item in stored.payload.files) != intent.byte_count):
        raise HTTPException(503, {"code": "PACKAGING_RETIREMENT_HISTORY_INVALID"})
    return action


def validate_receipt(value, context, command):
    try:
        result = PackagingRetirementReceipt.model_validate_json(value.model_dump_json(warnings="error"))
        result.verify(context.prepared, context.result, command)
        return result
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError, RecursionError):
        raise PackagingClientError() from None


def latest_dispatch(session, intent):
    return session.scalar(select(Dispatch).where(Dispatch.retirement_id == intent.id).order_by(Dispatch.ordinal.desc()).limit(1))


def verified_removal(session, intent):
    return session.scalar(select(Observation).where(Observation.retirement_id == intent.id, Observation.outcome == "REMOVED")
        .order_by(Observation.created_at, Observation.id).limit(1))


def view(session, intent):
    latest = latest_dispatch(session, intent); removed = verified_removal(session, intent)
    last_observed = session.scalar(select(Observation).where(Observation.dispatch_id == latest.id)) if latest else None
    return {"id": str(intent.id), "execution_id": str(intent.execution_id), "material_id": str(intent.material_id),
        "accepted_observation_id": str(intent.accepted_observation_id), "proof_sha256": intent.proof_sha256,
        "actor_id": str(intent.actor_id), "reason": intent.reason, "created_at": _aware(intent.created_at).isoformat(),
        "file_count": intent.file_count, "byte_count": intent.byte_count,
        "status": "REMOVED" if removed else "RECOVERY_REQUIRED" if last_observed else "RUNNING" if latest else "RESERVED",
        "last_dispatch_id": str(latest.id) if latest else None,
        "receipt": removed.receipt if removed else None}


def dispatch_view(session, action):
    observed = session.scalar(select(Observation).where(Observation.dispatch_id == action.id))
    return {"id": str(action.id), "ordinal": action.ordinal, "action": action.action, "actor_id": str(action.actor_id),
        "reason": action.reason, "created_at": _aware(action.created_at).isoformat(),
        "observation": None if observed is None else {"id": str(observed.id), "outcome": observed.outcome,
            "actor_current": observed.actor_current, "lease_current": observed.lease_current,
            "failure_code": observed.failure_code, "created_at": _aware(observed.created_at).isoformat()}}


def replay(session, actor_id, material_id, execution_id, key, digest, *, recovery=False):
    model = Dispatch if recovery else Intent
    prior = session.scalar(select(model).where(model.actor_id == actor_id, model.request_key == key))
    if prior is None: return None
    intent = session.get(Intent, prior.retirement_id) if recovery else prior
    if intent is None or intent.material_id != material_id or intent.execution_id != execution_id or prior.request_hash != digest:
        conflict("PACKAGING_RETIREMENT_REQUEST_KEY_REUSED")
    return intent


def audit(session, intent, actor_id, event, details):
    execution = session.get(MaterialPackagingExecution, intent.execution_id)
    session.add(MaterialAuditEvent(material_id=intent.material_id, actor_id=actor_id, event_type=event,
        generation=execution.input_snapshot["generation"], revision_hash=execution.input_snapshot["revision_hash"],
        result={"audit": {"execution_id": str(intent.execution_id), "retirement_id": str(intent.id), **details}}))
