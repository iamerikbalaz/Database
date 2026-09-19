"""Authenticated retirement orchestration using explicitly synthetic receipts."""
from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.api import packaging_retirement as api
from app.db.models import (InternalUser, UserCredential, MaterialAuditEvent,
    MaterialPackagingRetirement as Intent, MaterialPackagingRetirementDispatch as Dispatch,
    MaterialPackagingRetirementObservation as Observation, MaterialPackagingState)
from app.main import create_app
from app.material_review import canonical_hash
from app.packaging_client import PackagingClientError
from app.packaging_dispatch_lease import packaging_dispatch_lease
from app.packaging_retirement_contract import PackagingRetirementReceipt
from test_application_access import access_case
from test_material_approvals import approval_case
from test_packaging_actions import action_case
from test_packaging_downloads import download_case
from test_publication_staging import staging_case
from test_staging_reservations import PATH as STAGING_PATH, reservation, close_payload


class RemovalStub:
    def __init__(self): self.calls = []; self.callback = None; self.failure = None; self.corrupt = False

    def retire(self, prepared, report, accepted, command):
        self.calls.append(command)
        if self.callback: self.callback()
        if self.failure: raise self.failure
        # This response tests orchestration only; it never represents actual
        # filesystem deletion. Real HTTP/removal/restart tests live in the worker.
        value = {**command.document(prepared), "status": "REMOVED",
            "retirement_request_hash": canonical_hash(command.document(prepared)),
            "file_count": len(accepted.stored.payload.files), "byte_count": sum(item.size for item in accepted.stored.payload.files)}
        if self.corrupt: value["proof_sha256"] = "b" * 64
        return PackagingRetirementReceipt.model_validate(value)


def configure(item, *, enabled=True):
    settings = item.case.app.state.settings.model_copy(update={"packaging_retirement_enabled": enabled})
    item.case.app = create_app(settings, item.case.database, item.case.worker, inventory_client=item.inventory,
        technical_client=item.technical, packaging_client=item.worker)


@pytest.fixture
def retirement_case(staging_case):
    item = staging_case; item.removal = RemovalStub(); item.worker.retire = item.removal.retire
    configure(item)
    item.retirement_path = item.job_path + "/retirement"
    with item.case.client("ADMIN") as client:
        saved = client.get(item.job_path).json()
    item.retirement_body = {"idempotency_key": str(uuid4()), "expected_observation_id": saved["last_observation_id"],
        "expected_proof_sha256": saved["proof_sha256"], "acknowledgement": "REMOVE_LOCAL_COPY", "reason": "Remove reviewed synthetic local copy"}
    return item


def recover_body(saved, **changes):
    return {"idempotency_key": str(uuid4()), "expected_retirement_id": saved["id"],
        "expected_proof_sha256": saved["proof_sha256"], "expected_last_dispatch_id": saved["last_dispatch_id"],
        "acknowledgement": "REMOVE_LOCAL_COPY", "reason": "Verify the same synthetic retirement", **changes}


def test_existing_reader_finishes_while_new_reads_are_quarantined(retirement_case):
    from test_packaging_downloads import DATA
    item = retirement_case; recorded = []
    # This adapter models the worker's already-tested retention-lock BUSY result.
    # The assertion here concerns the application reauthorization boundary.
    item.removal.failure = PackagingClientError("PACKAGING_STORE_BUSY")
    def started_reader():
        with item.case.client("ADMIN") as admin:
            result = admin.post(item.retirement_path, json=item.retirement_body)
            assert result.status_code == 201 and result.json()["status"] == "RECOVERY_REQUIRED"
            recorded.append(result.json())
            assert admin.get(item.files_path).status_code == 409
            assert admin.get(item.download_path, params=item.params).status_code == 409
    item.download.callback = started_reader
    with item.case.client("LEADERSHIP") as reader:
        result = reader.get(item.download_path, params=item.params)
        assert result.status_code == 200 and result.content == DATA
    assert item.download.opened == item.download.closed == 1
    item.removal.failure = None; item.download.callback = None
    with item.case.client("ADMIN") as admin:
        result = admin.post(item.retirement_path + "/reconcile", json=recover_body(recorded[0]))
        assert result.status_code == 200 and result.json()["status"] == "REMOVED"
    assert item.removal.calls[0] == item.removal.calls[1]


def test_committed_intent_and_dispatch_precede_io_and_quarantine_downloads(retirement_case):
    item = retirement_case; source_calls = len(item.inventory.calls)
    def check_committed():
        with item.case.database.session() as session:
            intent = session.scalar(select(Intent)); action = session.scalar(select(Dispatch))
            assert intent is not None and action is not None and action.retirement_id == intent.id
            assert session.scalar(select(Observation)) is None
            assert session.get(MaterialPackagingState, UUID(item.saved["id"])).status == "PACKAGED"
        with item.case.client("LEADERSHIP") as reader:
            assert reader.get(item.files_path).status_code == 409
            assert reader.get(item.download_path, params=item.params).status_code == 409
            assert reader.get(item.retirement_path).json()["retirement"]["status"] == "RUNNING"
    item.removal.callback = check_committed
    with item.case.client("ADMIN") as client:
        before = client.get(item.job_path).json()
        result = client.post(item.retirement_path, json=item.retirement_body)
        assert result.status_code == 201 and result.json()["status"] == "REMOVED", result.json()
        saved = result.json()
        assert client.post(item.retirement_path, json=item.retirement_body).json() == saved
        assert client.get(item.retirement_path).json() == {"enabled": True, "retirement": saved}
        assert client.get(item.job_path).json() == before
        assert client.get(item.files_path).json()["detail"]["code"] == "PACKAGING_COPY_RETIRING"
        assert client.post(item.staging_path, json=item.staging_payload).status_code == 409
        history = client.get(item.retirement_path + "/dispatches").json()
        assert len(history["items"]) == 1 and history["items"][0]["observation"]["outcome"] == "REMOVED"
        assert history["items"][0]["observation"]["actor_current"] and history["items"][0]["observation"]["lease_current"]
        assert "issuer_session_id" not in result.text and "worker_request" not in result.text and "raw_content" not in result.text
    assert len(item.removal.calls) == 1 and len(item.inventory.calls) == source_calls and item.download.opened == 0


@pytest.mark.parametrize("role,status", [(None,401), ("PROCESSOR",403), ("OTHER",403), ("PRODUCTION_LEAD",403), ("LEADERSHIP",403)])
def test_only_current_admin_can_request_or_recover_retirement(retirement_case, role, status):
    item = retirement_case
    with item.case.client(role) as client:
        assert client.post(item.retirement_path, json=item.retirement_body).status_code == status
        body = recover_body({"id": str(uuid4()), "proof_sha256": "a" * 64, "last_dispatch_id": None})
        assert client.post(item.retirement_path + "/reconcile", json=body).status_code == status
    assert not item.removal.calls


@pytest.mark.parametrize("change,code", [("csrf",403), ("disabled",503), ("ack",422), ("reason",422), ("proof",409), ("observation",409)])
def test_rejected_command_has_no_retirement_or_worker_side_effect(retirement_case, change, code):
    item = retirement_case; body = dict(item.retirement_body)
    if change == "disabled": configure(item, enabled=False)
    elif change == "ack": body["acknowledgement"] = "INVALID"
    elif change == "reason": body["reason"] = " "
    elif change == "proof": body["expected_proof_sha256"] = "b" * 64
    elif change == "observation": body["expected_observation_id"] = str(uuid4())
    with item.case.client("ADMIN") as client:
        if change == "csrf": del client.headers["X-CSRF-Token"]
        assert client.post(item.retirement_path, json=body).status_code == code
    with item.case.database.session() as session:
        assert session.scalar(select(Intent)) is None and session.scalar(select(Dispatch)) is None
    assert not item.removal.calls


def test_lost_reply_requires_explicit_recovery_with_the_same_worker_command(retirement_case):
    item = retirement_case; item.removal.failure = PackagingClientError()
    with item.case.client("ADMIN") as client:
        first = client.post(item.retirement_path, json=item.retirement_body)
        assert first.status_code == 201 and first.json()["status"] == "RECOVERY_REQUIRED"
        saved = first.json(); body = recover_body(saved)
        assert client.post(item.retirement_path, json=item.retirement_body).json() == saved
        assert len(item.removal.calls) == 1 and client.get(item.files_path).status_code == 409
        item.removal.failure = None
        recovered = client.post(item.retirement_path + "/reconcile", json=body)
        assert recovered.status_code == 200 and recovered.json()["status"] == "REMOVED"
        assert client.post(item.retirement_path + "/reconcile", json=body).json() == recovered.json()
        assert client.post(item.retirement_path + "/reconcile", json=recover_body(recovered.json())).json() == recovered.json()
        page = client.get(item.retirement_path + "/dispatches", params={"limit":1}).json()
        next_page = client.get(item.retirement_path + "/dispatches", params={"after":page["next_cursor"]}).json()
        assert [row["ordinal"] for row in page["items"] + next_page["items"]] == [1,2]
        assert [row["action"] for row in page["items"] + next_page["items"]] == ["EXECUTE","RECONCILE"]
    assert len(item.removal.calls) == 2 and item.removal.calls[0] == item.removal.calls[1]


@pytest.mark.parametrize("change,code", [("disable",401), ("demote",403), ("password",403)])
def test_revocation_after_io_keeps_verified_fact_but_denies_original_actor(retirement_case, change, code):
    item = retirement_case; actor = item.case.users["ADMIN"].id
    def revoke():
        with item.case.database.session() as session:
            if change == "disable": session.get(InternalUser, actor).is_active = False
            elif change == "demote": session.get(InternalUser, actor).role = "LEADERSHIP"
            else: session.get(UserCredential, actor).must_change_password = True
            session.commit()
    item.removal.callback = revoke
    with item.case.client("ADMIN") as client:
        assert client.post(item.retirement_path, json=item.retirement_body).status_code == code
    with item.case.database.session() as session:
        observed = session.scalar(select(Observation)); assert observed.outcome == "REMOVED" and observed.actor_current is False
    with item.case.client("LEADERSHIP") as reader:
        assert reader.get(item.retirement_path).json()["retirement"]["status"] == "REMOVED"


def test_current_different_admin_can_recover_revoked_actors_uncertain_operation(retirement_case):
    item = retirement_case; item.removal.failure = PackagingClientError()
    with item.case.client("ADMIN") as client:
        saved = client.post(item.retirement_path, json=item.retirement_body).json()
    with item.case.database.session() as session:
        session.get(InternalUser, item.case.users["ADMIN"].id).is_active = False
        session.get(InternalUser, item.case.users["LEADERSHIP"].id).role = "ADMIN"
        session.commit()
    item.removal.failure = None
    with item.case.client("LEADERSHIP") as client:
        assert client.post(item.retirement_path + "/reconcile", json=recover_body(saved)).json()["status"] == "REMOVED"
    assert item.removal.calls[0] == item.removal.calls[1]
    with item.case.database.session() as session:
        actions = list(session.scalars(select(Dispatch).order_by(Dispatch.ordinal)))
        assert actions[0].actor_id != actions[1].actor_id


def test_active_staging_blocks_removal_and_closed_staging_history_is_preserved(retirement_case):
    item = retirement_case
    with item.case.client("ADMIN") as client:
        stage = client.post(STAGING_PATH, json=reservation(item, client)); assert stage.status_code == 201
        denied = client.post(item.retirement_path, json=item.retirement_body)
        assert denied.status_code == 409 and denied.json()["detail"]["code"] == "PACKAGING_RETIREMENT_STAGING_ACTIVE"
        closed = client.post(STAGING_PATH + "/" + stage.json()["id"] + "/close", json=close_payload(stage.json())).json()
        assert client.post(item.retirement_path, json=item.retirement_body).json()["status"] == "REMOVED"
        assert client.get(STAGING_PATH + "/" + closed["id"]).json() == closed
    assert len(item.removal.calls) == 1


def test_existing_dispatch_lease_excludes_retirement_before_intent(retirement_case):
    item = retirement_case
    with packaging_dispatch_lease(item.case.database.engine, UUID(item.saved["id"]), allow_test_sqlite=True):
        with item.case.client("ADMIN") as client:
            result = client.post(item.retirement_path, json=item.retirement_body)
            assert result.status_code == 409 and result.json()["detail"]["code"] == "PACKAGING_DISPATCH_BUSY"
    with item.case.database.session() as session: assert session.scalar(select(Intent)) is None


def test_lost_backend_lease_does_not_erase_verified_removal(retirement_case, monkeypatch):
    item = retirement_case; original = api.packaging_dispatch_lease; leases = []
    @contextmanager
    def capture(*args, **kwargs):
        with original(*args, **kwargs) as lease: leases.append(lease); yield lease
    monkeypatch.setattr(api, "packaging_dispatch_lease", capture)
    item.removal.callback = lambda: setattr(leases[0], "_active", False)
    with item.case.client("ADMIN") as client:
        result = client.post(item.retirement_path, json=item.retirement_body)
        assert result.status_code == 201 and result.json()["status"] == "REMOVED"
    with item.case.database.session() as session:
        observed = session.scalar(select(Observation)); assert observed.outcome == "REMOVED" and observed.lease_current is False


def test_corrupt_receipt_is_uncertain_and_changed_replay_cannot_replace_intent(retirement_case):
    item = retirement_case; item.removal.corrupt = True
    with item.case.client("ADMIN") as client:
        first = client.post(item.retirement_path, json=item.retirement_body)
        assert first.json()["status"] == "RECOVERY_REQUIRED"
        assert client.post(item.retirement_path, json={**item.retirement_body, "reason":"Changed intent"}).status_code == 409
        assert client.post(item.retirement_path, json={**item.retirement_body, "idempotency_key":str(uuid4())}).status_code == 409
        assert client.post(item.retirement_path + "/reconcile", json=recover_body(first.json(), expected_last_dispatch_id=str(uuid4()))).status_code == 409
    assert len(item.removal.calls) == 1


def test_http_reply_lost_after_database_commit_is_readable_without_reissuing_removal(retirement_case, monkeypatch):
    item = retirement_case; actual = api._commit; count = 0
    def lost(session):
        nonlocal count
        actual(session); count += 1
        if count == 2: raise ConnectionError("Synthetic lost response after retirement observation commit")
    with item.case.client("ADMIN") as client:
        with monkeypatch.context() as patch:
            patch.setattr(api, "_commit", lost)
            with pytest.raises(ConnectionError): client.post(item.retirement_path, json=item.retirement_body)
        saved = client.get(item.retirement_path).json()["retirement"]
        assert saved["status"] == "REMOVED"
        assert client.post(item.retirement_path, json=item.retirement_body).json() == saved
    assert len(item.removal.calls) == 1


def test_historical_retirement_needs_no_current_source_or_publication_approval(retirement_case):
    item = retirement_case; calls = len(item.inventory.calls)
    item.inventory.inventory = lambda *args: pytest.fail("Retirement must not read NAS")
    with item.case.client("ADMIN") as client:
        assert client.patch(item.material_path, json={"material_name": "Changed since historical packaging"}).status_code == 200
        assert client.post(item.retirement_path, json=item.retirement_body).json()["status"] == "REMOVED"
        audit = client.get(item.material_path + "/audit").json()
        events = [entry["event_type"] for entry in audit if entry["event_type"].startswith("PACKAGING_RETIREMENT_")]
        assert sorted(events) == ["PACKAGING_RETIREMENT_DISPATCHED", "PACKAGING_RETIREMENT_OBSERVED", "PACKAGING_RETIREMENT_REQUESTED"]
    assert len(item.inventory.calls) == calls


def test_disabling_the_feature_keeps_existing_quarantine_and_history(retirement_case):
    item = retirement_case; item.removal.failure = PackagingClientError()
    with item.case.client("ADMIN") as client:
        saved = client.post(item.retirement_path, json=item.retirement_body).json()
    configure(item, enabled=False)
    with item.case.client("ADMIN") as client:
        assert client.get(item.retirement_path).json() == {"enabled":False, "retirement":saved}
        assert client.get(item.files_path).status_code == 409
        assert client.post(item.staging_path, json=item.staging_payload).status_code == 409
        assert client.post(item.retirement_path + "/reconcile", json=recover_body(saved)).status_code == 503
        assert client.post(item.retirement_path, json=item.retirement_body).json() == saved
    assert len(item.removal.calls) == 1
