"""Current approved inputs and compact views for durable packaging executions."""
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from app.api.material_review import _material
from app.auth.service import _aware
from app.db.models import (MaterialInventory, MaterialTechnicalCheck, MaterialPackagingExecution,
    MaterialPackagingState, MaterialPackagingObservation, MaterialPackagingPolicy, PublicationBatchItem)
from app.material_identity import lock_folder_catalog, require_folder_idle, require_material_idle
from app.material_review import canonical_hash
from app.packaging_contract import PreparedPackaging
from app.packaging_policy import current_policy
from app.publication_preflight import _candidate


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


def approved_inputs(session, material_id, payload, access):
    """Caller holds the account gate; keep material → folder → brand lock order."""
    material = _material(session, material_id, access, lock=True)
    require_material_idle(session, material_id)
    lock_folder_catalog(session)
    if not material.folder_path: conflict("PACKAGING_FOLDER_REQUIRED")
    require_folder_idle(session, material.folder_path)
    item = session.get(PublicationBatchItem, (payload.batch_id, material_id))
    if item is None: raise HTTPException(404, "Publication batch item not found.")
    if item.snapshot_hash != payload.expected_snapshot_hash:
        conflict("PACKAGING_BATCH_SNAPSHOT_CHANGED")
    policy = current_policy(session, material_id)
    if policy is None: conflict("PACKAGING_POLICY_NOT_SELECTED")
    if policy.id != payload.expected_policy_id: conflict("PACKAGING_POLICY_CHANGED")
    snapshot, _ = _candidate(session, material)
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
        prepared = PreparedPackaging.model_validate_json(result.model_dump_json())
        prepared.verify_preparation(inputs)
        return prepared
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError, RecursionError):
        raise HTTPException(503, {"code": "PACKAGING_UNAVAILABLE"}) from None


def execution(session, material_id, execution_id):
    value = session.get(MaterialPackagingExecution, execution_id)
    if value is None or value.material_id != material_id:
        raise HTTPException(404, "Packaging execution not found.")
    return value


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
        "created_at": _aware(value.created_at).isoformat(), "updated_at": _aware(state.updated_at).isoformat()}


def view(session, value):
    policy = session.get(MaterialPackagingPolicy, value.policy_id)
    return {**summary(session, value), "reason": value.reason,
        "policy": {"id": str(policy.id), "revision": policy.revision, "policy": policy.policy, "storage_timezone": policy.storage_timezone},
        "approvals": {key: value.input_snapshot[key] for key in ("technical_approval_id", "publication_approval_id", "content_approval_id")}}
