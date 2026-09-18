"""Real application auth and immutable records; no staging or source IO occurs."""
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models import MaterialAuditEvent, MaterialPackagingDispatch, PublicationBatch
from app.main import create_app
from test_application_access import access_case
from test_material_approvals import approval_case
from test_packaging_actions import action_case
from test_packaging_downloads import download_case


@pytest.fixture
def staging_case(download_case):
    item = download_case
    settings = item.case.app.state.settings.model_copy(update={"gcs_bucket_name": "synthetic-reawote-staging",
        "gcs_staging_prefix": "isolated/contracts", "gcs_enabled": False})
    item.case.app = create_app(settings, item.case.database, item.case.worker, inventory_client=item.inventory,
        technical_client=item.technical, packaging_client=item.worker)
    with item.case.client("ADMIN") as client:
        job = client.get(item.job_path).json()
    item.staging_path = "/api/publication-batches/" + item.batch["id"] + "/staging-preview"
    item.staging_payload = {"job_id": str(uuid4()), "expected_snapshot_hash": item.batch["snapshot_hash"],
        "expected_csv_sha256": item.batch["csv_sha256"], "packages": [{"material_id": str(item.material.id),
            "execution_id": job["id"], "expected_observation_id": job["last_observation_id"],
            "expected_proof_sha256": job["proof_sha256"]}]}
    return item


def post(item, client):
    return client.post(item.staging_path, json=item.staging_payload)


def counts(item):
    with item.case.database.session() as session:
        return tuple(len(list(session.scalars(select(model)))) for model in
            (MaterialAuditEvent, MaterialPackagingDispatch, PublicationBatch))


def test_preview_loads_current_server_records_without_dispatch_mutation_or_secrets(staging_case):
    item = staging_case
    before = counts(item)
    calls = (len(item.worker.commands), len(item.inventory.calls), item.download.opened)
    with item.case.client("LEADERSHIP") as client:
        response = post(item, client)
        assert response.status_code == 200, response.json()
        value = response.json()
        assert post(item, client).json() == value
        assert not value["transfer_enabled"] and not value["importer_compatible"]
        assert value["layout"] == "INTERNAL_STAGING_V1"
        assert value["object_count"] == len(item.files["items"]) + 1
        assert value["materials"][0]["packaging_proof_sha256"] == item.params["proof_sha256"]
        csv = next(obj for obj in value["objects_preview"] if obj["material_id"] is None)
        assert csv["relative_path"] == "publication.csv" and csv["sha256"] == item.batch["csv_sha256"]
        assert value["total_bytes"] == sum(obj["size"] for obj in value["objects_preview"])
        assert response.headers["cache-control"] == "no-store"
        assert all(key not in response.text for key in ("worker_request\"", "source_inventory", "access_token", "service_token"))
        assert client.get(item.material_path).json()["is_published"] is False
    assert counts(item) == before
    assert calls == (len(item.worker.commands), len(item.inventory.calls), item.download.opened)


@pytest.mark.parametrize("role,expected", [(None,401), ("PROCESSOR",403), ("PRODUCTION_LEAD",403), ("ADMIN",200)])
def test_current_publication_roles_are_required(staging_case, role, expected):
    item = staging_case
    with item.case.client(role) as client:
        response = post(item, client)
        assert response.status_code == expected, response.json()
    assert item.download.opened == 0 and len(item.worker.commands) == 1


@pytest.mark.parametrize("kind", ["snapshot", "csv", "material", "execution", "observation", "proof", "duplicate", "job"])
def test_expected_bindings_and_complete_selection_fail_closed(staging_case, kind):
    item = staging_case
    expected = 409
    if kind == "snapshot": item.staging_payload["expected_snapshot_hash"] = "b" * 64
    elif kind == "csv": item.staging_payload["expected_csv_sha256"] = "b" * 64
    elif kind == "job": item.staging_payload["job_id"] = "00000000-0000-0000-0000-000000000001"; expected = 422
    elif kind == "duplicate": item.staging_payload["packages"] *= 2; expected = 422
    elif kind == "proof": item.staging_payload["packages"][0]["expected_proof_sha256"] = "b" * 64
    else:
        key = {"material": "material_id", "execution": "execution_id", "observation": "expected_observation_id"}[kind]
        item.staging_payload["packages"][0][key] = str(uuid4())
        if kind == "execution": expected = 404
    with item.case.client("ADMIN") as client:
        response = post(item, client)
        assert response.status_code == expected, response.json()
    assert item.download.opened == 0 and len(item.worker.commands) == 1


def test_historical_downloads_survive_edit_but_current_staging_preview_is_blocked(staging_case):
    item = staging_case
    with item.case.client("ADMIN") as client:
        assert post(item, client).status_code == 200
        assert client.patch(item.material_path, json={"material_name": "Changed after package approval"}).status_code == 200
        response = post(item, client)
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "PACKAGING_APPROVAL_CONTEXT_CHANGED"
        assert client.get(item.files_path).status_code == 200
    assert item.download.opened == 0 and len(item.inventory.calls) == 2


def test_no_configured_target_returns_fixed_error_without_external_access(staging_case):
    item = staging_case
    settings = item.case.app.state.settings.model_copy(update={"gcs_bucket_name": "", "gcs_staging_prefix": ""})
    item.case.app = create_app(settings, item.case.database, item.case.worker, inventory_client=item.inventory,
        technical_client=item.technical, packaging_client=item.worker)
    with item.case.client("ADMIN") as client:
        response = post(item, client)
        assert response.status_code == 503 and response.json()["detail"]["code"] == "GCS_TARGET_NOT_CONFIGURED"
    assert item.download.opened == 0


def test_aggregate_proof_limit_is_checked_before_reading_packaging_proofs(staging_case, monkeypatch):
    import app.publication_staging as domain
    item = staging_case
    monkeypatch.setattr(domain, "MAX_PROOF_TEXT", 1)
    with item.case.client("ADMIN") as client:
        response = post(item, client)
        assert response.status_code == 413 and response.json()["detail"]["code"] == "GCS_BATCH_PROOFS_TOO_LARGE"
    assert item.download.opened == 0


@pytest.mark.parametrize("kind", ["csrf", "password", "other-batch"])
def test_session_protection_and_same_material_package_from_another_batch(staging_case, kind):
    from app.db.models import UserCredential
    from test_publication_batches import creation
    from test_publication_preflight import preview
    item = staging_case
    with item.case.client("ADMIN") as client:
        if kind == "csrf":
            del client.headers["X-CSRF-Token"]
        elif kind == "password":
            with item.case.database.session() as session:
                session.get(UserCredential, item.case.users["ADMIN"].id).must_change_password = True
                session.commit()
        else:
            batch = client.post("/api/publication-batches", json=creation(preview(client, item.material.id))).json()
            item.staging_path = "/api/publication-batches/" + batch["id"] + "/staging-preview"
            item.staging_payload.update(expected_snapshot_hash=batch["snapshot_hash"], expected_csv_sha256=batch["csv_sha256"])
        response = post(item, client)
        assert response.status_code == (409 if kind == "other-batch" else 403), response.json()
        if kind == "other-batch":
            assert response.json()["detail"]["code"] == "GCS_PACKAGING_BATCH_MISMATCH"
    assert item.download.opened == 0
