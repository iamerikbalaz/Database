"""Durable internal reservations; none of these operations may perform cloud IO."""
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.db.models import (ImmutableAuditSnapshotError, PublicationStagingJob,
    PublicationStagingItem, PublicationStagingOwner, PublicationStagingClose)
from app.material_identity import require_folder_idle, require_brand_idle
from fastapi import HTTPException
from test_application_access import access_case
from test_material_approvals import approval_case
from test_packaging_actions import action_case
from test_packaging_downloads import download_case
from test_publication_staging import staging_case

PATH = "/api/publication-staging-jobs"


def reservation(item, client, **changes):
    preview = client.post(item.staging_path, json=item.staging_payload)
    assert preview.status_code == 200, preview.json()
    return {**item.staging_payload, "batch_id": item.batch["id"], "idempotency_key": str(uuid4()),
        "expected_plan_sha256": preview.json()["plan_sha256"], "reason": "Reserve isolated synthetic staging", **changes}


def close_payload(saved, **changes):
    return {"idempotency_key": str(uuid4()), "expected_plan_sha256": saved["plan_sha256"],
        "reason": "Close before cloud dispatch", **changes}


def test_reservation_is_exactly_replayable_and_blocks_changes_until_audited_close(staging_case):
    item = staging_case
    with item.case.client("ADMIN") as client:
        body = reservation(item, client)
        response = client.post(PATH, json=body)
        assert response.status_code == 201, response.json()
        saved = response.json(); path = PATH + "/" + saved["id"]
        assert saved["status"] == "RESERVED" and saved["close"] is None
        assert saved["id"] == body["job_id"] and saved["plan_sha256"] == body["expected_plan_sha256"]
        assert client.post(PATH, json=body).json() == saved
        assert client.get(path).json() == saved
        assert client.get(PATH).json()["items"][0]["id"] == saved["id"]
        assert client.patch(item.material_path, json={"material_name": "Blocked"}).status_code == 409
        assert client.get(item.files_path).status_code == 200
        closure = close_payload(saved)
        closed = client.post(path + "/close", json=closure)
        assert closed.status_code == 200, closed.json()
        assert closed.json()["status"] == "CLOSED" and closed.json()["close"]["reason"] == closure["reason"]
        assert client.post(path + "/close", json=closure).json() == closed.json()
        assert client.patch(item.material_path, json={"material_name": "Changed after closure"}).status_code == 200
        assert client.post(PATH, json=body).json() == closed.json()
        assert client.post(path + "/close", json=close_payload(saved)).status_code == 409
        assert client.get(item.material_path).json()["is_published"] is False
        audit = client.get(item.material_path + "/audit").json()
        assert len([event for event in audit if event["event_type"] == "PUBLICATION_STAGING_RESERVED"]) == 1
        assert len([event for event in audit if event["event_type"] == "PUBLICATION_STAGING_CLOSED"]) == 1
    assert item.download.opened == 0 and len(item.worker.commands) == 1 and len(item.inventory.calls) == 2
    with item.case.database.session() as session:
        owners = list(session.scalars(select(PublicationStagingOwner)))
        assert len(owners) == 1 and owners[0].active is False and owners[0].close_id is not None


@pytest.mark.parametrize("role,code", [(None,401), ("PROCESSOR",403), ("PRODUCTION_LEAD",403), ("LEADERSHIP",201)])
def test_reservation_and_history_roles(staging_case, role, code):
    item = staging_case
    with item.case.client("ADMIN") as admin:
        body = reservation(item, admin)
    with item.case.client(role) as client:
        response = client.post(PATH, json=body)
        assert response.status_code == code, response.json()
        assert client.get(PATH).status_code == (200 if code == 201 else code)
    assert item.download.opened == 0


@pytest.mark.parametrize("kind", ["plan", "same-key-changed-reason", "same-key-changed-batch", "second-active", "csrf"])
def test_reservation_rejects_changed_intent_without_duplicate_jobs(staging_case, kind):
    item = staging_case
    with item.case.client("ADMIN") as client:
        body = reservation(item, client)
        if kind in {"same-key-changed-reason", "same-key-changed-batch", "second-active"}:
            assert client.post(PATH, json=body).status_code == 201
        if kind == "plan": body["expected_plan_sha256"] = "b" * 64
        elif kind == "same-key-changed-reason": body["reason"] = "Different intent"
        elif kind == "same-key-changed-batch": body["batch_id"] = str(uuid4())
        elif kind == "second-active": body["idempotency_key"] = str(uuid4())
        else: del client.headers["X-CSRF-Token"]
        response = client.post(PATH, json=body)
        assert response.status_code == (403 if kind == "csrf" else 409), response.json()
    with item.case.database.session() as session:
        count = len(list(session.scalars(select(PublicationStagingJob))))
        assert count == (1 if kind.startswith("same-key") or kind == "second-active" else 0)


def test_active_owner_blocks_brand_and_overlapping_source_tree(staging_case):
    item = staging_case
    with item.case.client("ADMIN") as client:
        assert client.post(PATH, json=reservation(item, client)).status_code == 201
    with item.case.database.session() as session:
        saved = session.scalar(select(PublicationStagingItem))
        for value in (saved.folder_path, saved.folder_path.upper(), saved.folder_path.rsplit("/", 1)[0], saved.folder_path + "/PREVIEW"):
            with pytest.raises(HTTPException) as caught:
                require_folder_idle(session, value)
            assert caught.value.status_code == 409
        with pytest.raises(HTTPException): require_brand_idle(session, saved.brand_id)


@pytest.mark.parametrize("kind", ["plan", "role", "changed-replay"])
def test_close_is_proof_bound_authorized_and_exactly_idempotent(staging_case, kind):
    item = staging_case
    with item.case.client("ADMIN") as admin:
        saved = admin.post(PATH, json=reservation(item, admin)).json()
        path = PATH + "/" + saved["id"] + "/close"
        body = close_payload(saved)
        if kind == "changed-replay":
            assert admin.post(path, json=body).status_code == 200
            body["reason"] = "Changed closure intent"
        if kind == "plan": body["expected_plan_sha256"] = "b" * 64
        if kind == "role":
            with item.case.client("PROCESSOR") as actor:
                assert actor.post(path, json=body).status_code == 403
        else:
            assert admin.post(path, json=body).status_code == 409


@pytest.mark.parametrize("model", [PublicationStagingJob, PublicationStagingItem, PublicationStagingClose])
def test_reservation_provenance_cannot_be_rewritten_or_deleted(staging_case, model):
    item = staging_case
    with item.case.client("ADMIN") as client:
        saved = client.post(PATH, json=reservation(item, client)).json()
        assert client.post(PATH + "/" + saved["id"] + "/close", json=close_payload(saved)).status_code == 200
    with item.case.database.session() as session:
        record = session.scalar(select(model))
        if model is PublicationStagingItem: record.folder_path = "changed"
        else: record.reason = "Changed provenance"
        with pytest.raises(ImmutableAuditSnapshotError): session.commit()
        session.rollback()
        session.delete(session.scalar(select(model)))
        with pytest.raises(ImmutableAuditSnapshotError): session.commit()


def test_released_owner_cannot_be_reactivated_or_reassigned(staging_case):
    item = staging_case
    with item.case.client("ADMIN") as client:
        saved = client.post(PATH, json=reservation(item, client)).json()
        assert client.post(PATH + "/" + saved["id"] + "/close", json=close_payload(saved)).status_code == 200
    with item.case.database.session() as session:
        owner = session.get(PublicationStagingOwner, (UUID(saved["id"]), item.material.id))
        owner.active = True; owner.close_id = None
        with pytest.raises(ImmutableAuditSnapshotError): session.commit()


def test_sqlite_reservation_is_only_an_explicit_unit_test_aid(staging_case):
    from app.main import create_app
    item = staging_case
    with item.case.client("ADMIN") as client:
        body = reservation(item, client)
    settings = item.case.app.state.settings.model_copy(update={"app_env": "development"})
    item.case.app = create_app(settings, item.case.database, item.case.worker, inventory_client=item.inventory,
        technical_client=item.technical, packaging_client=item.worker)
    with item.case.client("ADMIN") as client:
        response = client.post(PATH, json=body)
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "GCS_STAGING_DATABASE_UNSUPPORTED"


@pytest.mark.parametrize("target", ["dispatch", "transfer", "observation", "result", "state"])
def test_staging_dispatch_facts_and_closed_progress_are_immutable_through_orm(staging_case, target):
    import staging_history_support as journal
    item = staging_case
    with item.case.client("ADMIN") as client:
        saved = client.post(PATH, json=reservation(item, client)).json()
    with item.case.database.session() as session:
        dispatched = journal.dispatch(session, UUID(saved["id"]))
        intent = journal.transfer(session, dispatched)
        observed = journal.observe(session, intent, outcome="UNCERTAIN", receipt=None, failure_code="GCS_OUTCOME_UNCERTAIN")
        result = journal.result(session, dispatched)
        journal.close(session, dispatched.job_id); session.commit()
        state = session.get(journal.PublicationStagingState, dispatched.job_id)
        record = {"dispatch": dispatched, "transfer": intent, "observation": observed, "result": result, "state": state}[target]
        field = {"dispatch": "reason", "transfer": "relative_path", "observation": "failure_code", "result": "failure_code", "state": "status"}[target]
        setattr(record, field, "CHANGED")
        with pytest.raises(ImmutableAuditSnapshotError): session.commit()
        session.rollback()
        session.delete(record)
        with pytest.raises(ImmutableAuditSnapshotError): session.commit()


def test_unsent_close_cannot_mislabel_a_previously_dispatched_job(staging_case):
    import staging_history_support as journal
    item = staging_case
    with item.case.client("ADMIN") as client:
        saved = client.post(PATH, json=reservation(item, client)).json()
        with item.case.database.session() as session:
            journal.dispatch(session, UUID(saved["id"])); session.commit()
        response = client.post(PATH + "/" + saved["id"] + "/close", json=close_payload(saved))
        assert response.status_code == 409 and response.json()["detail"]["code"] == "GCS_STAGING_ALREADY_DISPATCHED"
        assert client.get(PATH + "/" + saved["id"]).json()["status"] == "RUNNING"
    with item.case.database.session() as session:
        assert session.scalar(select(PublicationStagingClose)) is None
        assert session.scalar(select(PublicationStagingOwner.active)) is True
