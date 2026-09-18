"""Packaging provenance and shared mutation gates; actual concurrency is in PG tests."""
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import (ImmutableAuditSnapshotError, MaterialPackagingExecution, MaterialPackagingDispatch,
    MaterialPackagingObservation, MaterialPackagingState, MaterialPackagingPolicy, PublicationBatchItem, PBRMaterial)
from app.material_identity import require_material_idle, require_brand_idle, require_folder_idle
from app.material_review import canonical_hash
from test_application_access import access_case
from test_material_approvals import approval_case, run
from test_publication_preflight import prepare_candidate, preview
from test_publication_batches import creation, PATH
from test_packaging_policy import choose


def execution_values(session, batch_id, material_id, policy_id, actor_id, *, identifier=None):
    """Synthetic DB inputs only. Never represents real worker conversion success."""
    material = session.get(PBRMaterial, material_id)
    item = session.get(PublicationBatchItem, (batch_id, material_id))
    policy = session.get(MaterialPackagingPolicy, policy_id)
    identifier = identifier or uuid4()
    request = {"version": 1, "operation_id": str(identifier), "parts": material.folder_path.split("/"),
        "source_revision_hash": item.snapshot["source_revision_hash"], "technical_report_hash": item.snapshot["technical_report_hash"],
        "approval_context_hash": canonical_hash({"batch_snapshot": item.snapshot_hash, "policy_id": str(policy_id)}),
        "policy": policy.policy, "storage_timezone": policy.storage_timezone, "plan_hash": "a" * 64,
        "limits": {"seconds": 1800, "staged_bytes": 8 * 1024**3, "generated_bytes": 16 * 1024**3, "retained_bytes": 16 * 1024**3}}
    prepared = {"request": request, "request_hash": canonical_hash(request)}
    return {"id": identifier, "material_id": material_id, "batch_id": batch_id, "brand_id": material.published_brand_id,
        "policy_id": policy_id, "actor_id": actor_id, "issuer_session_id": uuid4(), "request_key": uuid4(),
        "request_hash": canonical_hash({"execution": str(identifier)}), "folder_path": material.folder_path,
        "reason": "Synthetic ownership fixture", "input_snapshot": json.loads(json.dumps(item.snapshot)),
        "input_hash": item.snapshot_hash, "worker_request": prepared, "worker_request_hash": prepared["request_hash"]}


def reserve(session, values):
    execution = MaterialPackagingExecution(**values); session.add(execution); session.flush()
    session.add(MaterialPackagingState(execution_id=execution.id, material_id=execution.material_id, status="RESERVED"))
    session.flush()
    return execution


def dispatch(session, execution, *, action="EXECUTE", ordinal=1):
    item = MaterialPackagingDispatch(execution_id=execution.id, ordinal=ordinal, actor_id=execution.actor_id,
        issuer_session_id=execution.issuer_session_id, action=action, request_key=uuid4(), request_hash="c" * 64,
        reason="Synthetic explicit action")
    session.add(item); session.flush()
    state = session.get(MaterialPackagingState, execution.id)
    state.status = "RUNNING"; state.last_dispatch_id = item.id; state.last_observation_id = None
    session.flush()
    return item


def finish(session, execution, action, *, outcome="UNCERTAIN", target="RECOVERY_REQUIRED", current=False):
    result = None if outcome in {"UNCERTAIN", "NOT_STARTED"} else {"version": 1, "operation_id": str(execution.id),
        "request_hash": execution.worker_request_hash, "status": outcome, "attempt": 1,
        "stored": {"proof_sha256": "d" * 64} if outcome == "READY" else None}
    observed = MaterialPackagingObservation(execution_id=execution.id, dispatch_id=action.id, outcome=outcome,
        worker_result=result, failure_code="PACKAGING_UNAVAILABLE" if outcome == "UNCERTAIN" else None,
        proof_sha256="d" * 64 if outcome == "READY" else None, inputs_current=current, actor_current=current)
    session.add(observed); session.flush()
    state = session.get(MaterialPackagingState, execution.id); state.status = target; state.last_observation_id = observed.id
    session.flush()
    return observed


@pytest.fixture
def packaging_owner_case(approval_case):
    case, worker, path = approval_case; material = prepare_candidate(case, worker, path)
    with case.client("ADMIN") as admin:
        policy = choose(admin, path)
        response = admin.post(PATH, json=creation(preview(admin, material.id)))
        assert response.status_code == 201
        batch_id = UUID(response.json()["id"])
    with case.database.session() as session:
        values = execution_values(session, batch_id, material.id, UUID(policy["id"]), case.users["ADMIN"].id)
        reserve(session, values); session.commit()
    return SimpleNamespace(case=case, material=material, path=path, values=values, execution_id=values["id"], batch_id=batch_id)


def test_active_owner_blocks_material_brand_and_overlapping_folder_mutations(packaging_owner_case):
    item = packaging_owner_case
    folder = item.values["folder_path"]
    with item.case.database.session() as session:
        for operation, argument, code in ((require_material_idle, item.material.id, "MATERIAL_OPERATION_ACTIVE"),
            (require_brand_idle, item.material.published_brand_id, "BRAND_OPERATION_ACTIVE"),
            *((require_folder_idle, path, "MATERIAL_OPERATION_ACTIVE") for path in (
                folder, folder.upper(), "library", folder + "/PREVIEW"))):
            with pytest.raises(HTTPException) as caught: operation(session, argument)
            assert caught.value.status_code == 409 and caught.value.detail["code"] == code
        require_material_idle(session, uuid4()); require_brand_idle(session, uuid4()); require_folder_idle(session, "unrelated")
    with item.case.client("ADMIN") as admin:
        assert admin.patch(item.path, json={"material_name": "Blocked change"}).status_code == 409
        assert run(admin, item.path).status_code == 409
        assert admin.get(item.path).status_code == 200
        assert admin.get(PATH + "/" + str(item.batch_id) + "/csv").status_code == 200


@pytest.mark.parametrize("state_name", ["RUNNING", "RETRY_REQUIRED", "RECOVERY_REQUIRED"])
def test_uncertain_or_incomplete_execution_keeps_mutation_ownership(packaging_owner_case, state_name):
    item = packaging_owner_case
    with item.case.database.session() as session:
        execution = session.get(MaterialPackagingExecution, item.execution_id)
        action = dispatch(session, execution)
        if state_name != "RUNNING":
            finish(session, execution, action, outcome="RETRY_REQUIRED" if state_name == "RETRY_REQUIRED" else "UNCERTAIN", target=state_name)
        session.commit()
        with pytest.raises(HTTPException): require_material_idle(session, item.material.id)


def test_unique_active_owner_is_enforced_and_completed_history_remains(packaging_owner_case):
    item = packaging_owner_case
    with item.case.database.session() as session:
        second = execution_values(session, item.batch_id, item.material.id, item.values["policy_id"], item.values["actor_id"])
        with pytest.raises(IntegrityError): reserve(session, second); session.commit()
        session.rollback()
        execution = session.get(MaterialPackagingExecution, item.execution_id)
        action = dispatch(session, execution)
        finish(session, execution, action, outcome="READY", target="PACKAGED", current=True); session.commit()
        require_material_idle(session, item.material.id)
        require_brand_idle(session, item.material.published_brand_id); require_folder_idle(session, item.values["folder_path"])
        reserve(session, second); session.commit()
        assert session.get(MaterialPackagingState, item.execution_id).status == "PACKAGED"


@pytest.mark.parametrize("model,field", [(MaterialPackagingExecution, "reason"), (MaterialPackagingDispatch, "reason"),
    (MaterialPackagingObservation, "failure_code")])
def test_packaging_input_dispatch_and_observation_orm_history_is_immutable(packaging_owner_case, model, field):
    item = packaging_owner_case
    with item.case.database.session() as session:
        execution = session.get(MaterialPackagingExecution, item.execution_id); action = dispatch(session, execution)
        observation = finish(session, execution, action); session.commit()
        identifier = {MaterialPackagingExecution: execution.id, MaterialPackagingDispatch: action.id, MaterialPackagingObservation: observation.id}[model]
        record = session.get(model, identifier); setattr(record, field, "Changed")
        with pytest.raises(ImmutableAuditSnapshotError): session.commit()
        session.rollback()
        session.delete(session.get(model, identifier))
        with pytest.raises(ImmutableAuditSnapshotError): session.commit()


@pytest.mark.parametrize("action", ["replace-material", "replace-execution", "delete"])
def test_ownership_identity_cannot_be_reassigned_or_deleted(packaging_owner_case, action):
    item = packaging_owner_case
    with item.case.database.session() as session:
        state = session.get(MaterialPackagingState, item.execution_id)
        if action == "replace-material": state.material_id = uuid4()
        elif action == "replace-execution": state.execution_id = uuid4()
        else: session.delete(state)
        with pytest.raises(ImmutableAuditSnapshotError): session.commit()


def test_observation_shape_cannot_claim_ready_without_proof(packaging_owner_case):
    item = packaging_owner_case
    with item.case.database.session() as session:
        execution = session.get(MaterialPackagingExecution, item.execution_id); action = dispatch(session, execution); session.commit()
        session.add(MaterialPackagingObservation(execution_id=execution.id, dispatch_id=action.id, outcome="READY",
            worker_result={"status": "READY"}, proof_sha256=None, failure_code=None, inputs_current=True, actor_current=True))
        with pytest.raises(IntegrityError): session.commit()
