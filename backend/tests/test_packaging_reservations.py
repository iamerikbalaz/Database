"""Authenticated reservation boundary; the worker stub prepares plans only."""
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.db.models import (InternalUser, MaterialAuditEvent, MaterialPackagingExecution,
    MaterialPackagingDispatch, MaterialPackagingObservation, MaterialPackagingState, PBRMaterial)
from app.main import create_app
from app.material_review import canonical_hash
from app.packaging_client import PackagingClientError
from app.packaging_contract import PackagingLimits, PreparedPackaging
from app.packaging_dispatch_lease import packaging_dispatch_lease
from test_application_access import access_case
from test_material_approvals import approval_case
from test_packaging_policy import choose
from test_packaging_ownership import dispatch
from test_publication_batches import PATH, creation
from test_publication_preflight import prepare_candidate, preview


class PreparationStub:
    def __init__(self):
        self.calls = []; self.callback = None; self.failure = None; self.substitute = False

    def prepare(self, payload):
        self.calls.append(json.loads(json.dumps(payload)))
        if self.callback: self.callback()
        if self.failure: raise self.failure
        request = {"version": 1, "operation_id": str(uuid4()) if self.substitute else payload["operation_id"],
            "parts": payload["parts"], "source_revision_hash": payload["expected_source_revision_hash"],
            "technical_report_hash": payload["expected_technical_report_hash"], "approval_context_hash": payload["approval_context_hash"],
            "policy": payload["policy"], "storage_timezone": payload["storage_timezone"], "plan_hash": "a" * 64,
            "limits": PackagingLimits().model_dump(mode="json")}
        return PreparedPackaging.model_validate({"request": request, "request_hash": canonical_hash(request)})

    def execute(self, *args, **kwargs): pytest.fail("Reservation must never execute packaging")
    def reconcile(self, *args, **kwargs): pytest.fail("Unsent reservation must never reconcile filesystem work")


def wire(case, technical, worker, *, enabled=True):
    settings = case.app.state.settings.model_copy(update={"app_env": "test", "packaging_enabled": enabled})
    case.app = create_app(settings, case.database, case.worker, technical_client=technical, packaging_client=worker)


@pytest.fixture
def reservation_case(approval_case):
    case, technical, path = approval_case
    material = prepare_candidate(case, technical, path)
    with case.client("ADMIN") as admin:
        policy = choose(admin, path)
        response = admin.post(PATH, json=creation(preview(admin, material.id)))
        assert response.status_code == 201
        batch = response.json()
    worker = PreparationStub(); wire(case, technical, worker)
    payload = {"idempotency_key": str(uuid4()), "batch_id": batch["id"],
        "expected_snapshot_hash": batch["items"][0]["snapshot_hash"], "expected_policy_id": policy["id"],
        "reason": "Prepare reviewed synthetic material"}
    return SimpleNamespace(case=case, material=material, path=path + "/packaging-executions", material_path=path,
        worker=worker, technical=technical, payload=payload, batch=batch)


def reserve(item, client=None, **changes):
    if client is None:
        with item.case.client("ADMIN") as admin: return reserve(item, admin, **changes)
    response = client.post(item.path, json={**item.payload, **changes})
    assert response.status_code == 201
    return response.json()


def close_body(**changes):
    return {"idempotency_key": str(uuid4()), "expected_last_dispatch_id": None, "reason": "Close unsent reservation", **changes}


def count(item, model):
    with item.case.database.session() as session: return len(list(session.scalars(select(model))))


def test_reservation_freezes_inputs_without_dispatch_or_publication(reservation_case):
    item = reservation_case
    with item.case.client("LEADERSHIP") as client:
        saved = reserve(item, client)
        assert saved["status"] == "RESERVED" and saved["last_dispatch_id"] is None and saved["proof_sha256"] is None
        assert saved["input_hash"] == item.payload["expected_snapshot_hash"]
        assert client.get(item.path + "/" + saved["id"]).json() == saved
        history = client.get(item.path).json()
        assert history["enabled"] and history["items"][0]["id"] == saved["id"]
        assert client.get(item.material_path).json()["is_published"] is False
        assert client.get(PATH + "/" + item.batch["id"] + "/csv").status_code == 200
        assert not any(word in json.dumps(saved) for word in ('"report":', '"worker_request":', '"issuer_session_id":', 'library/', 'source_content'))
    assert len(item.worker.calls) == 1 and count(item, MaterialPackagingExecution) == 1
    assert count(item, MaterialPackagingDispatch) == count(item, MaterialPackagingObservation) == 0
    with item.case.database.session() as session:
        record = session.get(MaterialPackagingExecution, UUID(saved["id"]))
        assert record.worker_request_hash == saved["worker_request_hash"]
        assert record.worker_request["request"]["operation_id"] == saved["id"]
        assert record.input_snapshot["technical_approval_id"] == saved["approvals"]["technical_approval_id"]


@pytest.mark.parametrize("role,code", [(None, 401), ("PROCESSOR", 403), ("OTHER", 403), ("PRODUCTION_LEAD", 403)])
def test_only_current_publication_roles_can_reserve_or_read(reservation_case, role, code):
    item = reservation_case
    with item.case.client(role) as client:
        assert client.post(item.path, json=item.payload).status_code == code
        assert client.get(item.path).status_code == code
        assert client.get(item.path + "/" + str(uuid4())).status_code == code
    assert not item.worker.calls and count(item, MaterialPackagingExecution) == 0


def test_disabled_service_stops_before_prepare_but_keeps_history_readable(reservation_case):
    item = reservation_case; wire(item.case, item.technical, item.worker, enabled=False)
    with item.case.client("ADMIN") as client:
        result = client.post(item.path, json=item.payload)
        assert result.status_code == 503 and result.json()["detail"]["code"] == "PACKAGING_SERVICE_DISABLED"
        assert client.get(item.path).json() == {"enabled": False, "items": [], "next_cursor": None}
    assert not item.worker.calls and count(item, MaterialPackagingExecution) == 0


@pytest.mark.parametrize("field,value,code", [("batch_id", str(uuid4()), 404),
    ("expected_snapshot_hash", "b" * 64, 409), ("expected_policy_id", str(uuid4()), 409)])
def test_stale_or_unrelated_review_selection_never_reaches_worker(reservation_case, field, value, code):
    item = reservation_case
    with item.case.client("ADMIN") as client:
        assert client.post(item.path, json={**item.payload, field: value}).status_code == code
    assert not item.worker.calls and count(item, MaterialPackagingExecution) == 0


@pytest.mark.parametrize("failure", ["unavailable", "substituted"])
def test_untrusted_prepare_cannot_create_an_owner(reservation_case, failure):
    item = reservation_case
    if failure == "unavailable": item.worker.failure = PackagingClientError("PACKAGING_SERVICE_UNAVAILABLE")
    else: item.worker.substitute = True
    with item.case.client("ADMIN") as client:
        assert client.post(item.path, json=item.payload).status_code == 503
    assert len(item.worker.calls) == 1 and count(item, MaterialPackagingExecution) == 0


@pytest.mark.parametrize("change,code", [("material", 409), ("disabled-account", 401), ("demoted-account", 403)])
def test_prepare_rechecks_actual_inputs_and_account_before_reserving(reservation_case, change, code):
    item = reservation_case
    def change_during_prepare():
        with item.case.database.session() as session:
            if change == "material": session.get(PBRMaterial, item.material.id).material_name = "Changed while planning"
            elif change == "disabled-account": session.get(InternalUser, item.case.users["ADMIN"].id).is_active = False
            else: session.get(InternalUser, item.case.users["ADMIN"].id).role = "PROCESSOR"
            session.commit()
    item.worker.callback = change_during_prepare
    with item.case.client("ADMIN") as client:
        assert client.post(item.path, json=item.payload).status_code == code
    assert len(item.worker.calls) == 1 and count(item, MaterialPackagingExecution) == 0


def test_exact_replay_returns_same_record_without_preparing_and_rejects_key_reuse(reservation_case):
    item = reservation_case
    with item.case.client("ADMIN") as client:
        first = reserve(item, client)
        assert reserve(item, client) == first
        changed = client.post(item.path, json={**item.payload, "reason": "Different request"})
        assert changed.status_code == 409 and changed.json()["detail"]["code"] == "PACKAGING_REQUEST_KEY_REUSED"
        other_path = f"/api/materials/{item.case.materials[1].id}/packaging-executions"
        assert client.post(other_path, json=item.payload).status_code == 409
    wire(item.case, item.technical, item.worker, enabled=False)
    assert reserve(item) == first
    assert len(item.worker.calls) == 1 and count(item, MaterialPackagingExecution) == 1


def test_second_reservation_and_content_mutation_are_blocked_by_owner(reservation_case):
    item = reservation_case; saved = reserve(item)
    with item.case.client("ADMIN") as client:
        assert client.post(item.path, json={**item.payload, "idempotency_key": str(uuid4())}).status_code == 409
        assert client.patch(item.material_path, json={"material_name": "Cannot change while reserved"}).status_code == 409
        assert client.post(item.material_path + "/reopen", json={"idempotency_key": str(uuid4()),
            "expected_generation": 0, "reason": "Must finish packaging first"}).status_code == 409
        other_path = f"/api/materials/{item.case.materials[1].id}/packaging-executions/" + saved["id"]
        assert client.get(other_path).status_code == 404
    assert len(item.worker.calls) == 1


def test_csrf_is_required_for_reserve_and_close(reservation_case):
    item = reservation_case
    with item.case.client("ADMIN") as client:
        token = client.headers.pop("X-CSRF-Token")
        assert client.post(item.path, json=item.payload).status_code == 403
        client.headers["X-CSRF-Token"] = token
        saved = reserve(item, client)
        client.headers.pop("X-CSRF-Token")
        assert client.post(item.path + "/" + saved["id"] + "/close", json=close_body()).status_code == 403
    assert count(item, MaterialPackagingDispatch) == 0


def test_admin_can_close_unsent_job_exactly_once_and_replay_original_reservation(reservation_case):
    item = reservation_case; saved = reserve(item); body = close_body()
    wire(item.case, item.technical, item.worker, enabled=False)
    with item.case.client("ADMIN") as client:
        result = client.post(item.path + "/" + saved["id"] + "/close", json=body)
        assert result.status_code == 200 and result.json()["status"] == "REJECTED"
        assert client.post(item.path + "/" + saved["id"] + "/close", json=body).json() == result.json()
        assert client.post(item.path, json=item.payload).json() == result.json()
        assert client.patch(item.material_path, json={"material_name": "Owner released"}).status_code == 200
    assert count(item, MaterialPackagingDispatch) == count(item, MaterialPackagingObservation) == 1
    with item.case.database.session() as session:
        observed = session.scalar(select(MaterialPackagingObservation))
        assert observed.outcome == "NOT_STARTED" and observed.worker_result is None
        events = list(session.scalars(select(MaterialAuditEvent).where(MaterialAuditEvent.event_type.in_(["PACKAGING_RESERVED", "PACKAGING_CLOSED"]))))
        assert len(events) == 2 and len({event.result["audit"]["execution_id"] for event in events}) == 1


def test_leadership_cannot_release_reserved_ownership(reservation_case):
    item = reservation_case; saved = reserve(item)
    with item.case.client("LEADERSHIP") as client:
        assert client.post(item.path + "/" + saved["id"] + "/close", json=close_body()).status_code == 403
    assert count(item, MaterialPackagingDispatch) == 0


def test_an_existing_dispatch_cannot_be_closed_as_not_started(reservation_case):
    item = reservation_case; saved = reserve(item)
    with item.case.database.session() as session:
        record = session.get(MaterialPackagingExecution, UUID(saved["id"]))
        action = dispatch(session, record); session.commit()
    with item.case.client("ADMIN") as client:
        result = client.post(item.path + "/" + saved["id"] + "/close", json=close_body(expected_last_dispatch_id=str(action.id)))
        assert result.status_code == 409 and result.json()["detail"]["code"] == "PACKAGING_RECONCILIATION_REQUIRED"
        assert client.get(item.path + "/" + saved["id"]).json()["status"] == "RUNNING"
    assert count(item, MaterialPackagingObservation) == 0


def test_busy_dispatch_lease_prevents_closure_without_database_writes(reservation_case):
    item = reservation_case; saved = reserve(item)
    with packaging_dispatch_lease(item.case.database.engine, UUID(saved["id"]), allow_test_sqlite=True):
        with item.case.client("ADMIN") as client:
            result = client.post(item.path + "/" + saved["id"] + "/close", json=close_body())
            assert result.status_code == 409 and result.json()["detail"]["code"] == "PACKAGING_DISPATCH_BUSY"
    assert count(item, MaterialPackagingDispatch) == 0


def test_history_cursor_is_bounded_scoped_and_has_no_duplicate_or_missing_jobs(reservation_case):
    item = reservation_case; expected = set()
    with item.case.client("ADMIN") as client:
        for _ in range(3):
            saved = reserve(item, client, idempotency_key=str(uuid4())); expected.add(saved["id"])
            assert client.post(item.path + "/" + saved["id"] + "/close", json=close_body()).status_code == 200
        seen = []; after = None
        for _ in range(4):
            page = client.get(item.path, params={"limit": 1, **({"after": after} if after else {})}).json()
            seen.extend(record["id"] for record in page["items"])
            after = page["next_cursor"]
            if after is None: break
        assert set(seen) == expected and len(seen) == 3
        assert client.get(item.path, params={"limit": 51}).status_code == 422
        other = f"/api/materials/{item.case.materials[1].id}/packaging-executions"
        assert client.get(other, params={"after": seen[0]}).status_code == 404
