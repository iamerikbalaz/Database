from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.db.models import (MaterialApproval, MaterialContentApproval, MaterialPackagingPolicy,
    PackagingSettingsRevision, PublicationBatchItem)
from app.inventory_client import InventoryClientError
from test_application_access import access_case
from test_material_approvals import approval_case, run
from test_packaging_reservations import PreparationStub, wire
from test_publication_batches import creation
from test_publication_preflight import prepare_candidate, preview

SETTINGS = "/api/settings/packaging"
BATCHES = "/api/publication-batches"


def update(version=0, **changes):
    return {"idempotency_key": str(uuid4()), "expected_version": version,
        "cutoff_date": "2026-03-05", "storage_timezone": "Europe/Prague", **changes}


@pytest.mark.parametrize("role,read,write", [(None, 401, 401), ("ADMIN", 200, 200),
    ("LEADERSHIP", 200, 403), ("PRODUCTION_LEAD", 200, 403), ("PROCESSOR", 200, 403)])
def test_settings_authentication_role_and_csrf(access_case, role, read, write):
    with access_case.client(role) as client:
        assert client.get(SETTINGS).status_code == read
        assert client.post(SETTINGS, json=update()).status_code == write
        if role:
            client.headers.pop("X-CSRF-Token")
            assert client.post(SETTINGS, json=update()).status_code == 403


def test_settings_defaults_exact_retry_stale_version_and_immutable_receipt(access_case):
    with access_case.client("ADMIN") as client:
        initial = client.get(SETTINGS).json()
        assert initial["version"] == 0 and initial["cutoff_date"] == "2026-03-04"
        assert initial["storage_timezone"] == "Europe/Prague"
        first = update(); saved = client.post(SETTINGS, json=first).json()
        assert saved["version"] == 1
        assert client.post(SETTINGS, json=update()).status_code == 409
        second = client.post(SETTINGS, json=update(1, cutoff_date="2026-03-06")).json()
        assert second["version"] == 2
        assert client.post(SETTINGS, json=first).json() == saved
        assert client.get(SETTINGS).json() == second
        assert client.post(SETTINGS, json={**first, "cutoff_date": "2027-01-01"}).status_code == 409
    with access_case.database.session() as session:
        saved = session.get(PackagingSettingsRevision, 1)
        saved.storage_timezone = "UTC"
        with pytest.raises(Exception, match="append-only"): session.commit()


@pytest.mark.parametrize("changes", [{"cutoff_date": "2026-02-30"}, {"cutoff_date": "1970-01-01"},
    {"storage_timezone": "Invalid/Zone"}, {"expected_version": True}, {"before_method": "B"}])
def test_settings_reject_invalid_or_unimplemented_parameters(access_case, changes):
    with access_case.client("ADMIN") as client:
        assert client.post(SETTINGS, json=update(**changes)).status_code == 422
        assert client.get(SETTINGS).json()["version"] == 0


def test_phase_one_csv_and_package_need_source_checks_but_no_human_approvals(approval_case):
    case, technical, path = approval_case
    material = prepare_candidate(case, technical, path, human_approvals=False)
    worker = PreparationStub(); wire(case, technical, worker)
    with case.client("LEADERSHIP") as client:
        # Leadership can run the automatic check; no approval or Checked is invented.
        before = client.get(path).json()["checked_status"]
        assert run(client, path).status_code == 200
        candidate = preview(client, material.id)
        assert candidate["can_prepare"]
        batch = client.post(BATCHES, json=creation(candidate, reason=None))
        assert batch.status_code == 201, batch.json()
        item = batch.json()["items"][0]
        assert all(item[field] is None for field in ("technical_approval_id", "publication_approval_id", "content_approval_id"))
        packaged = client.post(path + "/packaging-executions", json={"idempotency_key": str(uuid4()),
            "batch_id": batch.json()["id"], "expected_snapshot_hash": item["snapshot_hash"], "reason": "Synthetic package"})
        assert packaged.status_code == 201, packaged.json()
        assert client.get(path).json()["checked_status"] == before
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(MaterialApproval)) == 0
        assert session.scalar(select(func.count()).select_from(MaterialContentApproval)) == 0
        assert session.scalar(select(func.count()).select_from(MaterialPackagingPolicy)) == 1
    assert len(worker.calls) == 1


def test_cutoff_changes_future_batches_for_existing_material_without_rewriting_history(approval_case):
    case, technical, path = approval_case
    material = prepare_candidate(case, technical, path, human_approvals=False)
    worker = PreparationStub(); wire(case, technical, worker)
    with case.client("ADMIN") as client:
        first = client.post(BATCHES, json=creation(preview(client, material.id))).json()
        old_policy = client.get(path + "/packaging-policy").json()["current"]
        old_csv = client.get(BATCHES + "/" + first["id"] + "/csv").content
        old_preview = preview(client, material.id)
        assert old_policy["policy"] == "CURRENT_ON_OR_AFTER_2026_03_04"
        assert client.post(SETTINGS, json=update(cutoff_date="2099-01-01", storage_timezone="UTC")).status_code == 200
        assert client.post(BATCHES, json=creation(old_preview)).status_code == 409
        second = client.post(BATCHES, json=creation(preview(client, material.id))).json()
        new_policy = client.get(path + "/packaging-policy").json()["current"]
        assert new_policy["revision"] == 2 and new_policy["previous_id"] == old_policy["id"]
        assert new_policy["policy"] == "LEGACY_BEFORE_2026_03_04"
        assert new_policy["evidence"]["master_last_modified_at"] == old_policy["evidence"]["master_last_modified_at"]
        assert client.get(BATCHES + "/" + first["id"]).json() == first
        assert client.get(BATCHES + "/" + first["id"] + "/csv").content == old_csv
        # A job for the old batch continues to use that batch's original rule.
        old_job = client.post(path + "/packaging-executions", json={"idempotency_key": str(uuid4()),
            "batch_id": first["id"], "expected_snapshot_hash": first["items"][0]["snapshot_hash"], "reason": "Frozen old batch"})
        assert old_job.status_code == 201, old_job.json()
        assert old_job.json()["policy_id"] == old_policy["id"]
        assert worker.calls[0]["policy"] == old_policy["policy"]
        assert first["snapshot_hash"] != second["snapshot_hash"]
    with case.database.session() as session:
        original = session.get(PublicationBatchItem, (UUID(first["id"]), material.id))
        assert original.snapshot["packaging_settings"]["version"] == 0
        assert session.get(MaterialPackagingPolicy, UUID(old_policy["id"])).evidence == old_policy["evidence"]


def test_automatic_source_preparation_recovers_recorded_worker_failure_without_false_success(approval_case):
    case, technical, path = approval_case
    technical.failure = InventoryClientError("INVENTORY_UNAVAILABLE")
    with case.client("LEADERSHIP") as client:
        payload = {"idempotency_key": str(uuid4()), "expected_generation": client.get(path + "/review").json()["generation"]}
        first = client.post(path + "/technical-review/prepare", json=payload)
        assert first.status_code == 200
        assert first.json()["review"]["failure_code"] == "INVENTORY_UNAVAILABLE"
        assert first.json()["validation"] is None and first.json()["approvals"] == []
        technical.failure = None
        assert client.post(path + "/technical-review/prepare", json=payload).json() == first.json()
        assert not preview(client, case.materials[0].id)["can_prepare"]
