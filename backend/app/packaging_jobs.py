"""Current approved inputs and compact views for durable packaging executions."""
import json
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from app.api.material_review import _material
from app.auth.service import _aware
from app.db.models import (MaterialInventory, MaterialTechnicalCheck, MaterialPackagingExecution,
    MaterialPackagingState, MaterialPackagingObservation, MaterialPackagingPolicy, MaterialPackagingDispatch, PublicationBatchItem)
from app.inventory_client import InventoryClientError, SourceInventory
from app.material_identity import lock_folder_catalog, require_folder_idle, require_material_idle
from app.material_review import canonical_hash
from app.packaging_contract import PreparedPackaging, DispatchedPackagingResult
from app.packaging_client import PackagingClientError
from app.packaging_policy import current_policy
from app.publication_preflight import _candidate

MAX_PERSISTED_RESULT_TEXT = 32 * 1024**2 + 65536


def conflict(code):
    raise HTTPException(409, {"code": code})


def request_hash(operation, material_id, payload):
    return canonical_hash({"operation": operation, "material_id": str(material_id), "payload": payload.model_dump(mode="json")})


def replay(session, actor_id, material_id, key, digest):
    previous = session.scalar(select(MaterialPackagingExecution).where(MaterialPackagingExecution.actor_id == actor_id,
        MaterialPackagingExecution.request_key == key))
    if previous and (previous.material_id != material_id or previous.request_hash != digest):
        conflict("PACKAGING_REQUEST_KEY_REUSED")
    return previous


def frozen_report(session, snapshot):
    """Reconstruct from immutable records, never from a client-supplied report."""
    check = session.get(MaterialTechnicalCheck, UUID(snapshot["technical_check_id"]))
    inventory = session.get(MaterialInventory, UUID(snapshot["inventory_id"]))
    if (check is None or inventory is None or check.inventory_id != inventory.id
            or str(check.material_id) != snapshot["material"]["material_id"] or inventory.material_id != check.material_id
            or check.generation != snapshot["generation"] or inventory.generation != snapshot["generation"]
            or check.revision_hash != snapshot["revision_hash"] or inventory.revision_hash != snapshot["revision_hash"]
            or check.report_hash != snapshot["technical_report_hash"] or canonical_hash(check.report) != check.report_hash
            or inventory.source_inventory["source_revision_hash"] != snapshot["source_revision_hash"]):
        conflict("PACKAGING_APPROVED_REPORT_MISSING")
    return {**check.report, "inventory": inventory.source_inventory}


def approved_inputs(session, material_id, payload, access, *, packaging_execution_id=None, staging_job_id=None):
    """Caller holds the account gate; keep material → folder → brand lock order."""
    material = _material(session, material_id, access, lock=True)
    require_material_idle(session, material_id, packaging_execution_id=packaging_execution_id, staging_job_id=staging_job_id)
    lock_folder_catalog(session)
    if not material.folder_path: conflict("PACKAGING_FOLDER_REQUIRED")
    require_folder_idle(session, material.folder_path, packaging_execution_id=packaging_execution_id, staging_job_id=staging_job_id)
    item = session.get(PublicationBatchItem, (payload.batch_id, material_id))
    if item is None: raise HTTPException(404, "Publication batch item not found.")
    if item.snapshot_hash != payload.expected_snapshot_hash:
        conflict("PACKAGING_BATCH_SNAPSHOT_CHANGED")
    policy = current_policy(session, material_id)
    if policy is None: conflict("PACKAGING_POLICY_NOT_SELECTED")
    if policy.id != payload.expected_policy_id: conflict("PACKAGING_POLICY_CHANGED")
    snapshot, _ = _candidate(session, material, packaging_execution_id=packaging_execution_id, staging_job_id=staging_job_id)
    if snapshot["errors"] or snapshot != item.snapshot or canonical_hash(snapshot) != item.snapshot_hash:
        conflict("PACKAGING_APPROVAL_CONTEXT_CHANGED")
    report = frozen_report(session, snapshot)
    approval_hash = canonical_hash({"schema_version": 1, "batch_id": str(item.batch_id), "material_id": str(material_id),
        "snapshot_hash": item.snapshot_hash, "policy": {"id": str(policy.id), "revision": policy.revision,
            "policy": policy.policy, "storage_timezone": policy.storage_timezone, "evidence_hash": policy.evidence_hash}})
    return material, item, policy, report, approval_hash


def preparation(operation_id, material, item, policy, report, approval_hash):
    return {"operation_id": str(operation_id), "parts": material.folder_path.split("/"),
        "expected_source_revision_hash": item.snapshot["source_revision_hash"],
        "expected_technical_report_hash": item.snapshot["technical_report_hash"],
        "approval_context_hash": approval_hash, "policy": policy.policy, "storage_timezone": policy.storage_timezone,
        "report": report}


def validate_prepared(result, inputs):
    try:
        prepared = PreparedPackaging.model_validate_json(result.model_dump_json(warnings="error"))
        prepared.verify_preparation(inputs)
        return prepared
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError, RecursionError):
        raise HTTPException(503, {"code": "PACKAGING_UNAVAILABLE"}) from None


def execution(session, material_id, execution_id):
    value = session.get(MaterialPackagingExecution, execution_id)
    if value is None or value.material_id != material_id:
        raise HTTPException(404, "Packaging execution not found.")
    return value


def current_inputs(session, item, access, *, staging_job_id=None):
    selection = SimpleNamespace(batch_id=item.batch_id, expected_snapshot_hash=item.input_hash, expected_policy_id=item.policy_id)
    material, batch, policy, report, approval_hash = approved_inputs(session, item.material_id, selection, access,
        packaging_execution_id=item.id, staging_job_id=staging_job_id)
    prepared = PreparedPackaging.model_validate(item.worker_request)
    validate_prepared(prepared, preparation(item.id, material, batch, policy, report, approval_hash))
    if prepared.request_hash != item.worker_request_hash: conflict("PACKAGING_APPROVAL_CONTEXT_CHANGED")
    return prepared, report


def fresh_source(inventory_client, item):
    """Read-only observation: never replace the approved inventory/check IDs."""
    try:
        observed = SourceInventory.model_validate_json(inventory_client.inventory(item.folder_path).model_dump_json())
        if (observed.folder_name != item.input_snapshot["material"]["technical_identity"]
                or observed.source_revision_hash != item.input_snapshot["source_revision_hash"]):
            conflict("PACKAGING_SOURCE_CHANGED")
    except (InventoryClientError, ValueError, TypeError, KeyError, AttributeError, ArithmeticError, RecursionError):
        raise HTTPException(503, {"code": "PACKAGING_SOURCE_CHECK_FAILED"}) from None


def validate_dispatched(result, prepared, report, command):
    try:
        verified = DispatchedPackagingResult.model_validate_json(result.model_dump_json(warnings="error"))
        verified.verify_request(prepared, report)
        verified.verify_dispatch(command)
        # PostgreSQL's immutable guard bounds spaced jsonb text. Wire JSON may
        # be smaller; reject oversized evidence before any database INSERT.
        if len(json.dumps(verified.model_dump(mode="json"), ensure_ascii=True, allow_nan=False)) > MAX_PERSISTED_RESULT_TEXT:
            raise ValueError("Packaging evidence exceeds persistence limit")
        return verified
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError, RecursionError):
        raise PackagingClientError() from None


def dispatch_replay(session, item, actor_id, key, digest):
    prior = session.scalar(select(MaterialPackagingDispatch).where(MaterialPackagingDispatch.actor_id == actor_id,
        MaterialPackagingDispatch.request_key == key))
    if prior and (prior.execution_id != item.id or prior.request_hash != digest): conflict("PACKAGING_REQUEST_KEY_REUSED")
    return prior


def require_progress(session, item, payload, action):
    state = session.get(MaterialPackagingState, item.id)
    if state.last_dispatch_id != payload.expected_last_dispatch_id: conflict("PACKAGING_PROGRESS_CHANGED")
    if state.status in {"PACKAGED", "REJECTED"}: conflict("PACKAGING_EXECUTION_TERMINAL")
    if action == "EXECUTE" and (state.status != "RESERVED" or state.last_dispatch_id is not None):
        conflict("PACKAGING_RECONCILIATION_REQUIRED")
    if action == "RETRY" and state.status != "RETRY_REQUIRED": conflict("PACKAGING_RETRY_NOT_ALLOWED")
    if action == "RECONCILE" and state.last_dispatch_id is None: conflict("PACKAGING_NOT_DISPATCHED")
    # A lost CLOSE response still permanently forbids further conversion. The
    # application also enforces this from its own immutable dispatch history.
    if action in {"EXECUTE", "RETRY"} and session.scalar(select(MaterialPackagingDispatch.id).where(
            MaterialPackagingDispatch.execution_id == item.id, MaterialPackagingDispatch.action == "CLOSE").limit(1)):
        conflict("PACKAGING_EXECUTION_CLOSED")
    return state


def dispatch_view(session, action):
    observed = session.scalar(select(MaterialPackagingObservation).where(MaterialPackagingObservation.dispatch_id == action.id))
    return {"id": str(action.id), "ordinal": action.ordinal, "action": action.action, "actor_id": str(action.actor_id),
        "reason": action.reason, "created_at": _aware(action.created_at).isoformat(),
        "observation": None if observed is None else {"id": str(observed.id), "outcome": observed.outcome,
            "proof_sha256": observed.proof_sha256, "failure_code": observed.failure_code,
            "inputs_current": observed.inputs_current, "actor_current": observed.actor_current,
            "terminal": observed.worker_result.get("terminal") if observed.worker_result else None,
            "attempt": observed.worker_result.get("attempt") if observed.worker_result else None,
            "created_at": _aware(observed.created_at).isoformat()}}


def summary(session, value):
    state = session.get(MaterialPackagingState, value.id)
    if state is None: raise HTTPException(500, {"code": "PACKAGING_STATE_INVALID"})
    observation = session.get(MaterialPackagingObservation, state.last_observation_id) if state.last_observation_id else None
    return {"id": str(value.id), "material_id": str(value.material_id), "batch_id": str(value.batch_id),
        "actor_id": str(value.actor_id), "policy_id": str(value.policy_id), "status": state.status,
        "input_hash": value.input_hash, "worker_request_hash": value.worker_request_hash,
        "last_dispatch_id": str(state.last_dispatch_id) if state.last_dispatch_id else None,
        "last_observation_id": str(state.last_observation_id) if state.last_observation_id else None,
        "proof_sha256": observation.proof_sha256 if observation else None,
        "failure_code": observation.failure_code if observation else None,
        "inputs_current": observation.inputs_current if observation else None,
        "actor_current": observation.actor_current if observation else None,
        "terminal": observation.worker_result.get("terminal") if observation and observation.worker_result else None,
        "created_at": _aware(value.created_at).isoformat(), "updated_at": _aware(state.updated_at).isoformat()}


def view(session, value):
    policy = session.get(MaterialPackagingPolicy, value.policy_id)
    return {**summary(session, value), "reason": value.reason,
        "policy": {"id": str(policy.id), "revision": policy.revision, "policy": policy.policy, "storage_timezone": policy.storage_timezone},
        "approvals": {key: value.input_snapshot[key] for key in ("technical_approval_id", "publication_approval_id", "content_approval_id")}}
