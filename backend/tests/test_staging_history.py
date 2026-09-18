"""Real authorization and persistence; synthetic journal facts perform no IO."""
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.db.models import (InternalUser, MaterialAuditEvent, PublicationStagingClose,
    PublicationStagingState, PublicationStagingOwner)
from app.staging_dispatch_lease import staging_dispatch_lease
import staging_history_support as journal
from test_application_access import access_case
from test_material_approvals import approval_case
from test_packaging_actions import action_case
from test_packaging_downloads import download_case
from test_publication_staging import staging_case
from test_staging_reservations import PATH, reservation, close_payload


@pytest.fixture
def history_case(staging_case):
    item = staging_case
    with item.case.client("ADMIN") as client:
        item.saved = client.post(PATH, json=reservation(item, client)).json()
    item.job_id = UUID(item.saved["id"]); item.job_path = PATH + "/" + str(item.job_id)
    with item.case.database.session() as session:
        first = journal.dispatch(session, item.job_id)
        journal.observe(session, journal.transfer(session, first))
        journal.transfer(session, first, 2)
        journal.result(session, first)
        second = journal.dispatch(session, item.job_id)
        journal.observe(session, journal.transfer(session, second), outcome="UNCERTAIN", receipt=None,
            failure_code="GCS_OUTCOME_UNCERTAIN")
        journal.result(session, second); session.commit()
        item.dispatches = (first.id, second.id)
    item.history_path = item.job_path + "/dispatches"
    return item


def abandonment(item, **changes):
    return dict(idempotency_key=str(uuid4()), expected_plan_sha256=item.saved["plan_sha256"],
        expected_last_dispatch_id=str(item.dispatches[-1]), reason="Abandon uncertain synthetic transfer",
        acknowledge_possible_remote_effects=True) | changes


def test_bounded_history_includes_unobserved_intents_and_immutable_results(history_case):
    item = history_case
    with item.case.client("ADMIN") as client:
        first = client.get(item.history_path, params={"limit": 1})
        assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
        body = first.json(); assert body["next_cursor"] == 1
        assert body["items"][0]["action"] == "EXECUTE" and body["items"][0]["result"]["outcome"] == "UNCERTAIN"
        second = client.get(item.history_path, params={"after": 1, "limit": 1}).json()
        assert second["next_cursor"] is None and second["items"][0]["previous_dispatch_id"] == str(item.dispatches[0])
        path = item.history_path + "/" + str(item.dispatches[0]) + "/transfers"
        observed = client.get(path, params={"limit": 1}).json()
        assert observed["next_cursor"] == 1 and observed["items"][0]["observation"]["outcome"] == "VERIFIED"
        pending = client.get(path, params={"after": 1}).json()
        assert pending["items"][0]["ordinal"] == 2 and pending["items"][0]["observation"] is None
        assert client.get(item.history_path, params={"after": 2147483648}).status_code == 422
        assert client.get(path, params={"limit": 51}).status_code == 422
        assert client.get(item.history_path + "/" + str(uuid4()) + "/transfers").status_code == 404
        assert client.get(PATH + "/" + str(uuid4()) + "/dispatches").status_code == 404
    assert item.download.opened == 0


@pytest.mark.parametrize("role,read,write", [(None,401,401), ("PROCESSOR",403,403),
    ("PRODUCTION_LEAD",403,403), ("LEADERSHIP",200,403), ("ADMIN",200,200)])
def test_history_and_abandonment_use_current_least_privilege_roles(history_case, role, read, write):
    item = history_case
    with item.case.client(role) as client:
        assert client.get(item.history_path).status_code == read
        assert client.get(item.history_path + "/" + str(item.dispatches[0]) + "/transfers").status_code == read
        assert client.post(item.job_path + "/abandon", json=abandonment(item)).status_code == write


def test_abandonment_is_exactly_replayable_audited_and_preserves_journal(history_case):
    item = history_case
    with item.case.client("ADMIN") as client:
        before = client.get(item.history_path).json()
        payload = abandonment(item)
        response = client.post(item.job_path + "/abandon", json=payload)
        assert response.status_code == 200, response.json()
        closed = response.json()
        assert closed["status"] == "CLOSED" and closed["close"]["dispatched"] is True
        assert closed["last_dispatch_id"] == str(item.dispatches[-1]) and closed["last_result_id"]
        assert client.post(item.job_path + "/abandon", json=payload).json() == closed
        assert client.patch(item.material_path, json={"material_name": "Editable after explicit abandonment"}).status_code == 200
        assert client.post(item.job_path + "/abandon", json=payload).json() == closed
        assert client.get(item.history_path).json() == before
        assert client.post(item.job_path + "/abandon", json=abandonment(item)).status_code == 409
        assert client.post(item.job_path + "/close", json=close_payload(closed, idempotency_key=payload["idempotency_key"])).status_code == 409
        assert client.get(item.material_path).json()["is_published"] is False
    with item.case.database.session() as session:
        owners = list(session.scalars(select(PublicationStagingOwner).where(PublicationStagingOwner.job_id == item.job_id)))
        assert len(owners) == 1 and all(not row.active for row in owners)
        audit = list(session.scalars(select(MaterialAuditEvent).where(MaterialAuditEvent.event_type == "PUBLICATION_STAGING_ABANDONED")))
        assert len(audit) == 1 and audit[0].result["audit"]["remote_cancellation_confirmed"] is False
    assert item.download.opened == 0


@pytest.mark.parametrize("change", ["plan", "progress", "csrf", "changed-replay"])
def test_abandonment_rejects_changed_intent_or_missing_csrf(history_case, change):
    item = history_case
    with item.case.client("ADMIN") as client:
        payload = abandonment(item)
        if change == "plan": payload["expected_plan_sha256"] = "a" * 64
        elif change == "progress": payload["expected_last_dispatch_id"] = str(uuid4())
        elif change == "csrf": del client.headers["X-CSRF-Token"]
        else:
            assert client.post(item.job_path + "/abandon", json=payload).status_code == 200
            payload["reason"] = "Changed historical request"
        response = client.post(item.job_path + "/abandon", json=payload)
        assert response.status_code == (403 if change == "csrf" else 409)


@pytest.mark.parametrize("acknowledgment", [False, "true", 1, None])
def test_abandonment_requires_explicit_true_acknowledgment(history_case, acknowledgment):
    item = history_case
    with item.case.client("ADMIN") as client:
        assert client.post(item.job_path + "/abandon", json=abandonment(item,
            acknowledge_possible_remote_effects=acknowledgment)).status_code == 422
        assert client.get(item.job_path).json()["status"] == "RECOVERY_REQUIRED"


def test_abandonment_never_bypasses_an_inflight_dispatch_lease(history_case):
    item = history_case
    with item.case.client("ADMIN") as client:
        payload = abandonment(item)
        with staging_dispatch_lease(item.case.database.engine, item.job_id, allow_test_sqlite=True):
            blocked = client.post(item.job_path + "/abandon", json=payload)
            assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "GCS_DISPATCH_BUSY"
        assert client.get(item.job_path).json()["status"] == "RECOVERY_REQUIRED"
        assert client.post(item.job_path + "/abandon", json=payload).status_code == 200


def test_revoked_account_cannot_replay_abandonment_or_read_its_history(history_case):
    item = history_case
    with item.case.client("ADMIN") as client:
        payload = abandonment(item)
        assert client.post(item.job_path + "/abandon", json=payload).status_code == 200
        with item.case.database.session() as session:
            for actor in session.scalars(select(InternalUser).where(InternalUser.role == "ADMIN")):
                actor.is_active = False
            session.commit()
        assert client.get(item.history_path).status_code == 401
        assert client.post(item.job_path + "/abandon", json=payload).status_code == 401


def test_lost_lease_rolls_back_closure_owners_and_audit(history_case, monkeypatch):
    from contextlib import contextmanager
    from app.api import staging_history
    from app.staging_dispatch_lease import StagingLeaseError
    item = history_case
    @contextmanager
    def lost(*args, **kwargs):
        class Lease:
            def require_owned(self): raise StagingLeaseError("DISPATCH_LEASE_LOST")
        yield Lease()
    monkeypatch.setattr(staging_history, "staging_dispatch_lease", lost)
    with item.case.client("ADMIN") as client:
        response = client.post(item.job_path + "/abandon", json=abandonment(item))
        assert response.status_code == 503 and response.json()["detail"]["code"] == "GCS_DISPATCH_LEASE_LOST"
    with item.case.database.session() as session:
        assert session.get(PublicationStagingState, item.job_id).status == "RECOVERY_REQUIRED"
        assert session.scalar(select(PublicationStagingClose)) is None
        assert session.scalar(select(PublicationStagingOwner.active)) is True
        assert session.scalar(select(MaterialAuditEvent.id).where(MaterialAuditEvent.event_type == "PUBLICATION_STAGING_ABANDONED")) is None


def test_unsent_reservation_keeps_its_separate_close_action(staging_case):
    item = staging_case
    with item.case.client("ADMIN") as client:
        saved = client.post(PATH, json=reservation(item, client)).json()
        payload = dict(idempotency_key=str(uuid4()), expected_plan_sha256=saved["plan_sha256"],
            expected_last_dispatch_id=str(uuid4()), reason="Cannot abandon an unsent job",
            acknowledge_possible_remote_effects=True)
        path = PATH + "/" + saved["id"]
        response = client.post(path + "/abandon", json=payload)
        assert response.status_code == 409 and response.json()["detail"]["code"] == "GCS_STAGING_NOT_DISPATCHED"
        assert client.post(path + "/close", json=close_payload(saved)).status_code == 200
