import hashlib
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select

from app.db.models import MaterialAuditEvent, PublicationBatch, PublicationBatchItem
from test_application_access import access_case
from test_material_approvals import approval_case
from test_publication_preflight import prepare_candidate, preview

PATH = "/api/publication-batches"


def creation(view, **changes):
    return {"material_ids": [item["material_id"] for item in view["items"]],
        "expected_preview_hash": view["preview_hash"], "idempotency_key": str(uuid4()),
        "reason": "Prepare reviewed publication inputs", **changes}


def test_batch_and_exact_csv_are_immutable_after_later_material_edits(approval_case):
    case, worker, path = approval_case
    material = prepare_candidate(case, worker, path)
    with case.client("ADMIN") as client:
        view = preview(client, material.id); body = creation(view)
        response = client.post(PATH, json=body)
        assert response.status_code == 201
        batch = response.json(); batch_path = PATH + "/" + batch["id"]
        assert batch["status"] == "PREPARED" and batch["row_count"] == 1 and batch["snapshot_hash"] == view["preview_hash"]
        assert batch["items"][0]["snapshot_hash"] == view["items"][0]["snapshot_hash"]
        assert all(batch["items"][0][name] for name in ("technical_approval_id", "publication_approval_id", "content_approval_id", "metadata_snapshot_id"))
        artifact = client.get(batch_path + "/csv")
        assert artifact.status_code == 200 and artifact.content.startswith(b"\xef\xbb\xbfidentity_name;")
        assert hashlib.sha256(artifact.content).hexdigest() == batch["csv_sha256"] == artifact.headers["x-content-sha256"]
        assert artifact.headers["cache-control"] == "no-store" and artifact.headers["x-content-type-options"] == "nosniff"
        assert "attachment;" in artifact.headers["content-disposition"]
        assert client.post(PATH, json=body).json() == batch
        assert client.get(batch_path).json() == batch
        assert client.patch(path, json={"material_name": "Later material change"}).status_code == 200
        assert client.post(PATH, json=body).json() == batch
        assert client.get(batch_path + "/csv").content == artifact.content
        assert client.post(PATH, json={**body, "reason": "Different reason"}).status_code == 409
        assert client.post(PATH, json={**body, "idempotency_key": str(uuid4())}).json()["detail"]["code"] == "PUBLICATION_PREVIEW_CHANGED"
        assert client.get(path).json()["is_published"] is False
        events = [item for item in client.get(path + "/audit").json() if item["event_type"] == "PUBLICATION_BATCH_PREPARED"]
        assert len(events) == 1 and events[0]["details"]["batch_id"] == batch["id"]
        history = client.get(PATH).json()
        assert len(history["items"]) == 1 and history["items"][0]["id"] == batch["id"]
        assert "csv_bytes" not in response.text and "folder_path" not in response.text
    with case.database.session() as session:
        saved = session.get(PublicationBatch, UUID(batch["id"]))
        item = session.get(PublicationBatchItem, (saved.id, material.id))
        assert item.snapshot["material"]["material_name"] != "Later material change"
        saved.reason = "Rewrite forbidden"
        with pytest.raises(Exception, match="append-only"): session.commit()
    with case.database.session() as session:
        session.delete(session.get(PublicationBatchItem, (UUID(batch["id"]), material.id)))
        with pytest.raises(Exception, match="append-only"): session.commit()


@pytest.mark.parametrize("role,expected", [(None, 401), ("PROCESSOR", 403), ("OTHER", 403), ("PRODUCTION_LEAD", 403),
    ("ADMIN", 201), ("LEADERSHIP", 201)])
def test_server_roles_protect_creation_history_detail_and_csv(approval_case, role, expected):
    case, worker, path = approval_case
    material = prepare_candidate(case, worker, path)
    with case.client("ADMIN") as admin:
        body = creation(preview(admin, material.id))
        batch = admin.post(PATH, json=body).json()
    with case.client(role) as client:
        response = client.post(PATH, json={**body, "idempotency_key": str(uuid4())})
        assert response.status_code == expected
        for suffix in ("", "/" + batch["id"], "/" + batch["id"] + "/csv"):
            assert client.get(PATH + suffix).status_code == (200 if expected == 201 else expected)


def test_any_blocked_member_rejects_entire_batch_without_partial_audit(approval_case):
    case, worker, path = approval_case
    material = prepare_candidate(case, worker, path)
    with case.client("ADMIN") as admin:
        view = preview(admin, *[item.id for item in case.materials])
        assert not view["can_prepare"]
        response = admin.post(PATH, json=creation(view))
        assert response.status_code == 409 and response.json()["detail"]["code"] == "PUBLICATION_INPUTS_BLOCKED"
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(PublicationBatch)) == 0
        assert session.scalar(select(func.count()).select_from(PublicationBatchItem)) == 0
        assert session.scalar(select(func.count()).select_from(MaterialAuditEvent).where(MaterialAuditEvent.event_type == "PUBLICATION_BATCH_PREPARED")) == 0


def test_warning_acknowledgment_is_bound_to_request_and_saved_with_batch(approval_case):
    case, worker, path = approval_case
    material = prepare_candidate(case, worker, path, empty=True)
    with case.client("ADMIN") as admin:
        body = creation(preview(admin, material.id))
        response = admin.post(PATH, json=body)
        assert response.status_code == 422 and response.json()["detail"]["code"] == "PUBLICATION_WARNINGS_REQUIRE_ACKNOWLEDGMENT"
        result = admin.post(PATH, json={**body, "warnings_acknowledged": True})
        assert result.status_code == 201 and result.json()["warnings_acknowledged"] is True
        assert {warning["code"] for warning in result.json()["warnings"]} == {"CONTENT_DESCRIPTION_EMPTY", "CONTENT_TAGS_EMPTY"}
        assert admin.post(PATH, json=body).status_code == 409


def test_actor_request_keys_are_scoped_and_history_is_bounded_with_stable_cursors(approval_case):
    case, worker, path = approval_case
    material = prepare_candidate(case, worker, path)
    with case.client("ADMIN") as admin, case.client("LEADERSHIP") as leader:
        body = creation(preview(admin, material.id))
        first = admin.post(PATH, json=body).json()
        second = leader.post(PATH, json=body).json()
        assert first["id"] != second["id"]
        page = admin.get(PATH, params={"limit": 1}).json()
        assert len(page["items"]) == 1 and page["next_cursor"] == page["items"][0]["id"]
        older = admin.get(PATH, params={"limit": 1, "after": page["next_cursor"]}).json()
        assert len(older["items"]) == 1 and older["next_cursor"] is None
        assert {page["items"][0]["id"], older["items"][0]["id"]} == {first["id"], second["id"]}
        assert admin.get(PATH, params={"limit": 51}).status_code == 422
        assert admin.get(PATH, params={"after": str(uuid4())}).status_code == 404


def test_failed_item_insert_rolls_back_batch_and_every_audit_event(approval_case):
    case, worker, path = approval_case
    material = prepare_candidate(case, worker, path)
    def fail(*_): raise RuntimeError("Synthetic insert failure")
    event.listen(PublicationBatchItem, "before_insert", fail)
    try:
        with case.client("ADMIN") as admin:
            body = creation(preview(admin, material.id))
            with pytest.raises(RuntimeError, match="Synthetic insert failure"):
                admin.post(PATH, json=body)
    finally: event.remove(PublicationBatchItem, "before_insert", fail)
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(PublicationBatch)) == 0
        assert session.scalar(select(func.count()).select_from(PublicationBatchItem)) == 0
        assert session.scalar(select(func.count()).select_from(MaterialAuditEvent).where(MaterialAuditEvent.event_type == "PUBLICATION_BATCH_PREPARED")) == 0


def test_reordered_selection_replays_the_same_batch_and_never_adds_internal_csv_columns(approval_case):
    case, worker, path = approval_case
    for material in case.materials: prepare_candidate(case, worker, f"/api/materials/{material.id}")
    with case.client("ADMIN") as admin:
        body = creation(preview(admin, *[item.id for item in case.materials]))
        first = admin.post(PATH, json=body)
        assert first.status_code == 201
        assert admin.post(PATH, json={**body, "material_ids": list(reversed(body["material_ids"]))}).json() == first.json()
        items = first.json()["items"]
        assert [item["ordinal"] for item in items] == [1, 2]
        assert [item["row"]["identity_name"] for item in items] == sorted(material.technical_identity for material in case.materials)


def test_creation_requires_csrf_and_never_reflects_extra_values(access_case):
    with access_case.client("ADMIN") as admin:
        body = creation(preview(admin, access_case.materials[0].id))
        response = admin.post(PATH, json={**body, "arbitrary-private-field": "do not reflect"})
        assert response.status_code == 422 and "do not reflect" not in response.text and "arbitrary-private-field" not in response.text
        admin.headers.pop("X-CSRF-Token")
        assert admin.post(PATH, json=body).status_code == 403
