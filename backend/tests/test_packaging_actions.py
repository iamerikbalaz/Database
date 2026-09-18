"""Application orchestration with synthetic proofs; actual IO is tested separately."""
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.db.models import (AuthSession, InternalUser, MaterialInventory, MaterialPackagingExecution, MaterialPackagingDispatch,
    MaterialPackagingObservation, MaterialPackagingState, MaterialTechnicalCheck, PBRMaterial, UserCredential)
from app.inventory_client import InventoryClientError, SourceInventory
from app.main import create_app
from app.material_review import canonical_hash
from app.packaging_client import PackagingClientError
from app.packaging_contract import DispatchedPackagingResult, PreparedPackaging
from app.packaging_dispatch_lease import packaging_dispatch_lease
from test_application_access import access_case
from test_inventory_client import rehash as inventory_hash
from test_material_approvals import approval_case
from test_packaging_client import fixture, rehash
from test_packaging_policy import choose
from test_packaging_reservations import PreparationStub, close_body, count, reserve
from test_publication_batches import PATH, creation
from test_publication_preflight import prepare_candidate, preview


class DispatchStub(PreparationStub):
    def __init__(self):
        super().__init__()
        self.template = fixture("packaging-contract-square.json")
        self.commands = []; self.on_dispatch = None; self.dispatch_failure = None
        self.ready = True; self.terminal = None; self.corrupt = None

    def prepare(self, payload):
        value = super().prepare(payload).model_dump(mode="json")
        value["request"]["plan_hash"] = self.template["prepared"]["request"]["plan_hash"]
        value["request_hash"] = canonical_hash(value["request"])
        return PreparedPackaging.model_validate(value)

    def dispatch(self, prepared, report, command):
        self.commands.append(command)
        if self.on_dispatch: self.on_dispatch()
        if self.dispatch_failure: raise self.dispatch_failure
        # Rebind the exported shape to this synthetic database fixture. This mock
        # does not assert that ZIP bytes were produced for the substituted identity.
        value = json.loads(json.dumps(self.template["result"]).replace(
            self.template["report"]["inventory"]["folder_name"], report["inventory"]["folder_name"]))
        value.update(operation_id=prepared.request.operation_id, request_hash=prepared.request_hash,
            dispatch=command.model_dump(mode="json"), terminal=self.terminal or ("CLOSED" if command.action == "CLOSE" else "OPEN"))
        if self.ready:
            value["stored"]["payload"]["bundle"]["operation_id"] = prepared.request.operation_id
            value["stored"]["payload"]["bundle"]["storage_timezone"] = prepared.request.storage_timezone
            for archive in value["stored"]["payload"]["bundle"]["archives"]:
                archive["storage_timezone"] = prepared.request.storage_timezone
                archive["policy"] = prepared.request.policy
            rehash(value)
        else: value.update(status="RETRY_REQUIRED", stored=None)
        result = DispatchedPackagingResult.model_validate(value)
        result.verify_request(prepared, report)
        if self.corrupt: result = result.model_copy(update=self.corrupt)
        return result


class FreshInventory:
    def __init__(self, technical):
        self.technical = technical; self.calls = []; self.callback = None; self.failure = None; self.changed = False

    def inventory(self, path):
        self.calls.append(path)
        if self.callback: self.callback()
        if self.failure: raise self.failure
        value = self.technical.validate(path).inventory.model_dump(mode="json")
        if self.changed:
            next(entry for entry in value["entries"] if entry["kind"] == "file")["sha256"] = "e" * 64
            inventory_hash(value)
        return SourceInventory.model_validate_json(json.dumps(value))


@pytest.fixture
def action_case(approval_case):
    case, technical, path = approval_case
    worker = DispatchStub()
    material = prepare_candidate(case, technical, path, report=worker.template["report"])
    with case.client("ADMIN") as client:
        policy = choose(client, path)
        batch = client.post(PATH, json=creation(preview(client, material.id))).json()
    inventory = FreshInventory(technical)
    settings = case.app.state.settings.model_copy(update={"app_env": "test", "packaging_enabled": True})
    case.app = create_app(settings, case.database, case.worker, inventory_client=inventory, technical_client=technical, packaging_client=worker)
    item = SimpleNamespace(case=case, material=material, path=path + "/packaging-executions", material_path=path,
        worker=worker, technical=technical, inventory=inventory, batch=batch,
        payload={"idempotency_key": str(uuid4()), "batch_id": batch["id"], "expected_snapshot_hash": batch["items"][0]["snapshot_hash"],
            "expected_policy_id": policy["id"], "reason": "Synthetic action fixture"})
    item.saved = reserve(item)
    item.job_path = item.path + "/" + item.saved["id"]
    return item


def act(item, client, action="run", body=None):
    current = client.get(item.job_path).json()
    return client.post(item.job_path + "/" + action,
        json=body or close_body(expected_last_dispatch_id=current["last_dispatch_id"]))


def observation(item):
    with item.case.database.session() as session:
        state = session.get(MaterialPackagingState, UUID(item.saved["id"]))
        return session.get(MaterialPackagingObservation, state.last_observation_id)


def test_run_records_command_before_io_accepts_current_proof_and_releases_owner(action_case):
    item = action_case
    before = (count(item, MaterialInventory), count(item, MaterialTechnicalCheck))
    def check_committed():
        with item.case.database.session() as session:
            state = session.get(MaterialPackagingState, UUID(item.saved["id"]))
            assert state.status == "RUNNING" and state.last_observation_id is None
            command = session.get(MaterialPackagingDispatch, state.last_dispatch_id)
            assert (command.action, command.ordinal) == ("EXECUTE", 1)
    item.worker.on_dispatch = check_committed
    with item.case.client("LEADERSHIP") as client:
        result = act(item, client)
        assert result.status_code == 200 and result.json()["status"] == "PACKAGED", result.json()
        assert result.json()["inputs_current"] and result.json()["actor_current"]
        assert client.get(item.material_path).json()["is_published"] is False
    assert len(item.inventory.calls) == 2 and len(item.worker.commands) == 1
    assert before == (count(item, MaterialInventory), count(item, MaterialTechnicalCheck))
    with item.case.client("ADMIN") as client:
        assert client.patch(item.material_path, json={"material_name": "Now editable"}).status_code == 200


def test_lost_response_requires_reconcile_and_exact_replay_never_dispatches(action_case):
    item = action_case; item.worker.dispatch_failure = PackagingClientError()
    body = close_body()
    with item.case.client("ADMIN") as client:
        first = act(item, client, body=body)
        assert first.json()["status"] == "RECOVERY_REQUIRED" and first.json()["failure_code"] == "PACKAGING_UNAVAILABLE"
        assert act(item, client, body=body).json() == first.json()
        assert act(item, client, "retry").status_code == 409
        assert act(item, client, body={**body, "reason": "Changed"}).status_code == 409
        item.worker.dispatch_failure = None
        result = act(item, client, "reconcile")
        assert result.json()["status"] == "PACKAGED", result.json()
    assert [c.action for c in item.worker.commands] == ["EXECUTE", "RECONCILE"]
    assert [c.ordinal for c in item.worker.commands] == [1, 2]


def test_explicit_retry_requires_known_incomplete_work_and_reads_source_again(action_case):
    item = action_case; item.worker.ready = False
    with item.case.client("ADMIN") as client:
        assert act(item, client, "retry").status_code == 409
        assert act(item, client, "reconcile").status_code == 409
        first = act(item, client)
        assert first.json()["status"] == "RETRY_REQUIRED", first.json()
        item.worker.ready = True
        assert act(item, client, "retry").json()["status"] == "PACKAGED"
    assert len(item.inventory.calls) == 3 and [c.action for c in item.worker.commands] == ["EXECUTE", "RETRY"]


@pytest.mark.parametrize("offline", [False, True])
def test_changed_or_unavailable_source_stops_before_any_dispatch(action_case, offline):
    item = action_case
    if offline: item.inventory.failure = InventoryClientError()
    else: item.inventory.changed = True
    with item.case.client("ADMIN") as client:
        response = act(item, client)
        assert response.status_code == (503 if offline else 409)
        assert client.get(item.job_path).json()["status"] == "RESERVED"
    assert not item.worker.commands and count(item, MaterialPackagingDispatch) == 0


@pytest.mark.parametrize("change", ["source", "offline", "approval"])
def test_ready_output_is_preserved_but_not_accepted_after_current_input_change(action_case, change):
    item = action_case
    def changed():
        if change == "source": item.inventory.changed = True
        elif change == "offline": item.inventory.failure = InventoryClientError()
        else:
            with item.case.database.session() as session:
                session.get(PBRMaterial, item.material.id).material_name = "External data change"
                session.commit()
    item.worker.on_dispatch = changed
    with item.case.client("ADMIN") as client:
        result = act(item, client)
        assert result.json()["status"] == "RECOVERY_REQUIRED", result.json()
        assert result.json()["proof_sha256"] and not result.json()["inputs_current"]
        item.worker.on_dispatch = None
        assert act(item, client, "close").json()["status"] == "REJECTED"
    assert observation(item).outcome == "READY"


@pytest.mark.parametrize("change,code", [("disable", 401), ("demote", 403), ("reset", 403), ("session", 401)])
def test_result_is_audited_but_not_accepted_after_account_change(action_case, change, code):
    item = action_case
    def changed():
        with item.case.database.session() as session:
            identifier = item.case.users["LEADERSHIP"].id
            if change == "disable": session.get(InternalUser, identifier).is_active = False
            elif change == "demote": session.get(InternalUser, identifier).role = "PROCESSOR"
            elif change == "reset": session.get(UserCredential, identifier).must_change_password = True
            else:
                from app.auth.service import database_now
                for value in session.scalars(select(AuthSession).where(AuthSession.user_id == identifier)):
                    value.revoked_at = database_now(session)
            session.commit()
    item.worker.on_dispatch = changed
    with item.case.client("LEADERSHIP") as client:
        assert act(item, client).status_code == code
    assert observation(item).outcome == "READY" and not observation(item).actor_current
    item.worker.on_dispatch = None
    with item.case.client("ADMIN") as client:
        assert client.get(item.job_path).json()["status"] == "RECOVERY_REQUIRED"
        assert act(item, client, "reconcile").json()["status"] == "PACKAGED"


def test_recovery_and_fenced_closure_work_offline_without_claiming_not_started(action_case):
    item = action_case; item.worker.dispatch_failure = PackagingClientError()
    with item.case.client("ADMIN") as client:
        assert act(item, client).json()["status"] == "RECOVERY_REQUIRED"
        item.inventory.failure = InventoryClientError(); calls = len(item.inventory.calls)
        item.worker.dispatch_failure = None; item.worker.ready = False
        assert act(item, client, "reconcile").json()["status"] == "RETRY_REQUIRED"
        assert act(item, client, "close").json()["status"] == "REJECTED"
        assert len(item.inventory.calls) == calls
    assert observation(item).outcome == "RETRY_REQUIRED" and observation(item).worker_result["terminal"] == "CLOSED"


def test_close_intent_and_closing_reconciliation_permanently_block_retry(action_case):
    item = action_case; item.worker.ready = False
    with item.case.client("ADMIN") as client:
        assert act(item, client).json()["status"] == "RETRY_REQUIRED"
        item.worker.dispatch_failure = PackagingClientError()
        assert act(item, client, "close").json()["status"] == "RECOVERY_REQUIRED"
        item.worker.dispatch_failure = None; item.worker.terminal = "CLOSING"
        assert act(item, client, "reconcile").json()["status"] == "RECOVERY_REQUIRED"
        item.worker.terminal = "OPEN"
        assert act(item, client, "reconcile").json()["status"] == "RETRY_REQUIRED"
        assert act(item, client, "retry").json()["detail"]["code"] == "PACKAGING_EXECUTION_CLOSED"
        item.worker.terminal = "CLOSED"
        assert act(item, client, "reconcile").json()["status"] == "RECOVERY_REQUIRED"
        assert act(item, client, "close").json()["status"] == "REJECTED"


@pytest.mark.parametrize("change", ["operation_id", "request_hash", "terminal", "dispatch", "stored"])
def test_injected_client_cannot_bypass_independent_result_validation(action_case, change):
    item = action_case
    item.worker.corrupt = {change: {"operation_id": str(uuid4()), "request_hash": "f" * 64, "terminal": "CLOSED",
        "dispatch": {"id": str(uuid4()), "ordinal": 1, "action": "EXECUTE"}, "stored": None}[change]}
    with item.case.client("ADMIN") as client:
        result = act(item, client)
        assert result.json()["status"] == "RECOVERY_REQUIRED" and result.json()["proof_sha256"] is None
    assert observation(item).outcome == "UNCERTAIN" and observation(item).failure_code == "PACKAGING_UNAVAILABLE"


def test_progress_lease_and_history_are_bounded_without_private_payloads(action_case):
    item = action_case; item.worker.ready = False
    with item.case.client("ADMIN") as client:
        with packaging_dispatch_lease(item.case.database.engine, UUID(item.saved["id"]), allow_test_sqlite=True):
            assert act(item, client).json()["detail"]["code"] == "PACKAGING_DISPATCH_BUSY"
        initial = close_body()
        assert act(item, client, body=initial).json()["status"] == "RETRY_REQUIRED"
        assert act(item, client, "reconcile", body=close_body()).json()["detail"]["code"] == "PACKAGING_PROGRESS_CHANGED"
        assert act(item, client, "reconcile").json()["status"] == "RETRY_REQUIRED"
        first = client.get(item.job_path + "/dispatches", params={"limit": 1}).json()
        second = client.get(item.job_path + "/dispatches", params={"after": first["next_cursor"], "limit": 1}).json()
        assert [first["items"][0]["ordinal"], second["items"][0]["ordinal"]] == [1, 2] and second["next_cursor"] is None
        assert client.get(item.job_path + "/dispatches", params={"limit": 51}).status_code == 422
        assert not any(word in json.dumps(first) for word in ('"report":', '"worker_request":', '"issuer_session_id":', 'library/'))
    with item.case.client("LEADERSHIP") as client:
        assert act(item, client, "close").status_code == 403


@pytest.mark.parametrize("role,code", [(None, 401), ("PROCESSOR", 403), ("OTHER", 403), ("PRODUCTION_LEAD", 403)])
def test_dispatch_actions_and_history_require_publication_roles(action_case, role, code):
    item = action_case
    with item.case.client(role) as client:
        for action in ("run", "retry", "reconcile", "close"):
            assert client.post(item.job_path + "/" + action, json=close_body()).status_code == code
        assert client.get(item.job_path + "/dispatches").status_code == code
    assert not item.worker.commands


def test_persistence_limit_records_uncertainty_without_inserting_oversized_evidence(action_case, monkeypatch):
    from app import packaging_jobs
    item = action_case; monkeypatch.setattr(packaging_jobs, "MAX_PERSISTED_RESULT_TEXT", 256)
    with item.case.client("ADMIN") as client:
        result = act(item, client)
        assert result.json()["status"] == "RECOVERY_REQUIRED"
    assert observation(item).outcome == "UNCERTAIN" and observation(item).worker_result is None


def test_lost_lease_after_io_preserves_result_without_accepting_it(action_case, monkeypatch):
    from contextlib import contextmanager
    from app.api import packaging_jobs
    item = action_case; original = packaging_jobs.packaging_dispatch_lease; leases = []
    @contextmanager
    def capture(*args, **kwargs):
        with original(*args, **kwargs) as lease:
            leases.append(lease); yield lease
    monkeypatch.setattr(packaging_jobs, "packaging_dispatch_lease", capture)
    item.worker.on_dispatch = lambda: setattr(leases[0], "_active", False)
    with item.case.client("ADMIN") as client:
        result = act(item, client)
        assert result.json()["status"] == "RECOVERY_REQUIRED" and result.json()["proof_sha256"]
        item.worker.on_dispatch = None
        assert act(item, client, "reconcile").json()["status"] == "PACKAGED"
