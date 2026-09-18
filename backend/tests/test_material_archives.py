"""Real authorization and reversible local lifecycle, without source mutations."""
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.api.material_archives import build_material_archives_router
from app.db.models import MaterialLifecycleEvent, MaterialLifecycleState, MaterialInventory, MaterialReviewState, PBRMaterial, PBRMaterialMetadata, PublishedBrand
from test_application_access import access_case
from test_material_review import review_case, scan
from test_material_approvals import approval_case
from test_packaging_actions import action_case
from test_packaging_downloads import download_case, DATA
from test_material_identity import identity_case, prepare as prepare_identity
from test_publication_staging import staging_case


def attach(case):
    if "/api/material-archives" not in case.app.openapi()["paths"]:
        case.app.include_router(build_material_archives_router(case.database))
        case.app.openapi_schema = None
    return case


@pytest.fixture
def archive_case(access_case):
    return attach(access_case)


def path(identifier):
    return f"/api/material-archives/{identifier}"


def prepare(client, identifier, action="ARCHIVE", *, key=None):
    response = client.get(path(identifier) + "/preview", params={"action": action})
    assert response.status_code == 200, response.json()
    view = response.json()
    assert view["can_apply"], view
    return {"action": action, "request_key": str(key or uuid4()), "expected_version": view["version"],
        "expected_input_sha256": view["input_sha256"], "reason": "Synthetic lifecycle decision", "acknowledge": True}


def apply(client, identifier, payload):
    return client.post(path(identifier) + "/commands", json=payload)


def test_archive_restore_and_exact_replay_preserve_identity_and_do_not_repeat(archive_case):
    case = archive_case; material = case.materials[0]; identifier = material.id
    with case.client("ADMIN") as client:
        original = client.get(f"/api/materials/{identifier}").json()
        payload = prepare(client, identifier)
        response = apply(client, identifier, payload)
        assert response.status_code == 200, response.json()
        archived = response.json(); item = archived["event"]
        assert item["action"] == "ARCHIVE" and item["version"] == 1 and item["actor_id"] == str(case.users["ADMIN"].id)
        assert item["review_generation"] == 1 and item["input_sha256"] == payload["expected_input_sha256"]
        assert client.get(f"/api/materials/{identifier}").status_code == 404
        assert str(identifier) not in [value["id"] for value in client.get("/api/materials").json()]
        listing = client.get("/api/material-archives").json()
        assert [value["material"]["id"] for value in listing["items"]] == [str(identifier)]
        assert listing["items"][0]["is_archived"] and listing["next_cursor"] is None
        assert apply(client, identifier, payload).json() == archived
        assert client.get(path(identifier) + "/commands/" + payload["request_key"]).json() == archived
        restored = apply(client, identifier, prepare(client, identifier, "RESTORE"))
        assert restored.status_code == 200 and restored.json()["event"]["version"] == 2
        assert not client.get(path(identifier)).json()["is_archived"]
        assert client.get("/api/material-archives").json()["items"] == []
        current = client.get(f"/api/materials/{identifier}").json()
        for field in ("id", "sequence_number", "technical_identity", "folder_path", "assigned_processor_id", "project_id", "published_brand_id"):
            assert current[field] == original[field]
        assert current["workflow_status"] == "IN_PROGRESS" and current["validation_status"] == "NOT_CHECKED"
        assert apply(client, identifier, payload).json() == archived
        assert not client.get(path(identifier)).json()["is_archived"]
        assert apply(client, identifier, {**payload, "reason": "Changed reason"}).status_code == 409
        assert apply(client, case.materials[1].id, payload).status_code == 409
        history = client.get(path(identifier) + "/history").json()
        assert [item["version"] for item in history["items"]] == [2, 1]
        assert client.get(path(identifier) + "/history", params={"after": history["items"][0]["id"]}).json()["items"] == [archived["event"]]
        assert client.get(path(case.materials[1].id) + "/history", params={"after": item["id"]}).status_code == 409
    with case.database.session() as session:
        assert session.get(PublishedBrand, material.published_brand_id).next_sequence_number == 3
        assert session.scalar(select(func.count()).select_from(PBRMaterial)) == 2
        assert session.scalar(select(func.count()).select_from(MaterialLifecycleEvent)) == 2


@pytest.mark.parametrize("role,status", [(None, 401), ("PROCESSOR", 403), ("OTHER", 403), ("LEADERSHIP", 403), ("PRODUCTION_LEAD", 403)])
def test_every_lifecycle_surface_is_admin_only(archive_case, role, status):
    case = archive_case; identifier = case.materials[0].id
    with case.client("ADMIN") as admin: payload = prepare(admin, identifier)
    with case.client(role) as client:
        for url in ("/api/material-archives", path(identifier), path(identifier) + "/history",
                path(identifier) + "/preview?action=ARCHIVE", path(identifier) + "/commands/" + payload["request_key"]):
            assert client.get(url).status_code == status
        assert apply(client, identifier, payload).status_code == status
    with case.database.session() as session:
        assert session.get(MaterialLifecycleState, identifier) is None


@pytest.mark.parametrize("publication,published", [("PREPARING", False), ("UPLOADED_WAITING_FOR_IMPORT", False),
    ("WAITING_FOR_VERIFICATION", False), ("PUBLISHED_CURRENT", True), ("PUBLISHED_UPDATE_REQUIRED", True),
    ("PUBLICATION_ERROR", False), ("NOT_PUBLISHED", True)])
def test_unreconciled_publication_is_blocked(archive_case, publication, published):
    case = archive_case; identifier = case.materials[0].id
    with case.client("ADMIN") as client:
        payload = prepare(client, identifier)
        with case.database.session() as session:
            material = session.get(PBRMaterial, identifier)
            material.publication_status = publication; material.is_published = published; session.commit()
        preview = client.get(path(identifier) + "/preview?action=ARCHIVE").json()
        assert not preview["can_apply"] and preview["blocked_code"] == "MATERIAL_LIFECYCLE_PUBLICATION_BLOCKED"
        payload.update(expected_input_sha256=preview["input_sha256"])
        assert apply(client, identifier, payload).json()["detail"]["code"] == preview["blocked_code"]


def test_stale_preview_and_missing_csrf_do_not_archive(archive_case):
    case = archive_case; identifier = case.materials[0].id
    with case.client("ADMIN") as client:
        payload = prepare(client, identifier)
        assert client.patch(f"/api/materials/{identifier}", json={"material_name": "New reviewed name"}).status_code == 200
        response = apply(client, identifier, payload)
        assert response.status_code == 409 and response.json()["detail"]["code"] == "MATERIAL_LIFECYCLE_INPUT_CHANGED"
        payload = prepare(client, identifier)
        client.headers.pop("X-CSRF-Token")
        assert apply(client, identifier, payload).status_code == 403
    with case.database.session() as session: assert session.get(MaterialLifecycleState, identifier) is None


@pytest.mark.parametrize("change", [{"request_key": str(UUID(int=0))}, {"acknowledge": False}, {"acknowledge": 1},
    {"reason": "  "}, {"reason": "bad\x00reason"}, {"expected_version": True}, {"expected_input_sha256": "bad"}])
def test_command_requires_reviewed_strict_fields(archive_case, change):
    case = archive_case; identifier = case.materials[0].id
    with case.client("ADMIN") as client:
        response = apply(client, identifier, prepare(client, identifier) | change)
        assert response.status_code == 422
    with case.database.session() as session: assert session.get(MaterialLifecycleState, identifier) is None


def test_archived_work_surfaces_are_hidden_for_every_role(archive_case):
    case = archive_case; identifier = case.materials[0].id; material_path = f"/api/materials/{identifier}"
    with case.client("ADMIN") as client: assert apply(client, identifier, prepare(client, identifier)).status_code == 200
    for role in ("ADMIN", "PRODUCTION_LEAD", "LEADERSHIP", "PROCESSOR", "OTHER"):
        with case.client(role) as client:
            for suffix in ("", "/metadata", "/metadata/snapshots", "/review", "/inventory", "/audit", "/technical-review",
                    "/content", "/content-history", "/previews", "/identity-operations", "/identity-history", "/publishing-context", "/content-sources", "/content-drafts"):
                assert client.get(material_path + suffix).status_code == 404, suffix
            if role in {"ADMIN", "PRODUCTION_LEAD", "PROCESSOR", "OTHER"}:
                assert client.patch(material_path, json={"material_name": "Denied edit"}).status_code == 404
                assert client.post(material_path + "/inventory/scan", json={"idempotency_key": str(uuid4()), "expected_generation": 1}).status_code == 404
    with case.client("ADMIN") as client:
        assert client.get(material_path + "/history").status_code == 200
        assert client.get(path(identifier)).status_code == 200


def test_review_is_invalidated_but_immutable_inventory_is_preserved(review_case):
    case, worker, material_path = review_case; attach(case); identifier = case.materials[0].id
    with case.client("ADMIN") as client:
        observed = scan(client, material_path).json()
        archived = apply(client, identifier, prepare(client, identifier))
        assert archived.status_code == 200 and archived.json()["event"]["review_generation"] == observed["generation"] + 1
        restored = apply(client, identifier, prepare(client, identifier, "RESTORE"))
        assert restored.status_code == 200
        view = client.get(material_path + "/review").json()
        assert view["generation"] == observed["generation"] + 2
        assert view["inventory_id"] is None and view["revision_hash"] is None and view["checked_at"] is None
    assert len(worker.calls) == 1
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(MaterialInventory)) == 1


def test_history_failure_rolls_back_material_review_and_lifecycle(archive_case):
    case = archive_case; identifier = case.materials[0].id
    def reject(session, *_):
        if any(isinstance(item, MaterialLifecycleEvent) for item in session.new): raise RuntimeError("Synthetic lifecycle audit failure")
    with case.client("ADMIN") as client:
        payload = prepare(client, identifier)
        event.listen(Session, "before_flush", reject)
        try:
            with pytest.raises(RuntimeError, match="Synthetic lifecycle audit failure"):
                apply(client, identifier, payload)
        finally: event.remove(Session, "before_flush", reject)
        assert client.get(f"/api/materials/{identifier}").status_code == 200
        assert client.get(path(identifier) + "/commands/" + payload["request_key"]).status_code == 404
    with case.database.session() as session:
        assert session.get(MaterialLifecycleState, identifier) is None
        assert session.get(MaterialReviewState, identifier) is None


def test_active_packaging_owner_blocks_archive(action_case):
    item = action_case; attach(item.case)
    with item.case.client("ADMIN") as client:
        view = client.get(path(item.material.id) + "/preview?action=ARCHIVE").json()
        assert not view["can_apply"] and view["blocked_code"] == "MATERIAL_OPERATION_ACTIVE"
        payload = {"action": "ARCHIVE", "request_key": str(uuid4()), "expected_version": view["version"],
            "expected_input_sha256": view["input_sha256"], "reason": "Blocked decision", "acknowledge": True}
        assert apply(client, item.material.id, payload).json()["detail"]["code"] == "MATERIAL_OPERATION_ACTIVE"
    assert not item.worker.commands


def test_active_identity_owner_blocks_archive_before_source_result(identity_case):
    case, worker, material_path, target = identity_case
    identifier = case.materials[0].id
    def inspect_owned_material():
        with case.client("ADMIN") as admin:
            view = admin.get(path(identifier) + "/preview?action=ARCHIVE").json()
            assert not view["can_apply"] and view["blocked_code"] == "MATERIAL_OPERATION_ACTIVE"
            body = {"action": "ARCHIVE", "request_key": str(uuid4()), "expected_version": view["version"],
                "expected_input_sha256": view["input_sha256"], "reason": "Blocked decision", "acknowledge": True}
            assert apply(admin, identifier, body).json()["detail"]["code"] == "MATERIAL_OPERATION_ACTIVE"
    worker.execute_callback = inspect_owned_material
    with case.client("ADMIN") as client:
        assert client.post(material_path + "/identity-confirm", json=prepare_identity(client, material_path, target)).status_code == 200
        assert apply(client, identifier, prepare(client, identifier)).status_code == 200


@pytest.mark.parametrize("dispatched", [False, True])
def test_staging_ownership_and_closed_external_attempt_have_distinct_archive_rules(staging_case, dispatched):
    from test_staging_reservations import PATH, reservation
    import staging_history_support as journal
    item = staging_case; identifier = item.material.id
    with item.case.client("ADMIN") as client:
        saved = client.post(PATH, json=reservation(item, client))
        assert saved.status_code == 201
        job_id = UUID(saved.json()["id"])
        active = client.get(path(identifier) + "/preview?action=ARCHIVE").json()
        assert not active["can_apply"] and active["blocked_code"] == "MATERIAL_OPERATION_ACTIVE"
        # Synthetic journal facts; neither branch contacts cloud storage.
        with item.case.database.session() as session:
            if dispatched: journal.dispatch(session, job_id)
            journal.close(session, job_id, dispatched=dispatched); session.commit()
        view = client.get(path(identifier) + "/preview?action=ARCHIVE").json()
        body = {"action": "ARCHIVE", "request_key": str(uuid4()), "expected_version": view["version"],
            "expected_input_sha256": view["input_sha256"], "reason": "Reviewed closure", "acknowledge": True}
        response = apply(client, identifier, body)
        if dispatched:
            assert not view["can_apply"] and view["blocked_code"] == "MATERIAL_LIFECYCLE_EXTERNAL_STATE_BLOCKED"
            assert response.status_code == 409 and response.json()["detail"]["code"] == view["blocked_code"]
        else:
            assert view["can_apply"] and response.status_code == 200
        assert client.get(f'/api/publication-batches/{item.batch["id"]}/csv').status_code == 200
    assert item.download.opened == 0


def test_accepted_download_and_export_remain_available_after_archive(download_case):
    item = download_case; attach(item.case)
    with item.case.client("ADMIN") as client:
        before = client.get(item.material_path).json()
        result = apply(client, item.material.id, prepare(client, item.material.id))
        assert result.status_code == 200, result.json()
        assert client.get(item.material_path).status_code == 404
        assert client.get(item.files_path).json() == item.files
        history = client.get(item.path).json()
        assert history["archived"] and history["items"][0]["status"] == "PACKAGED"
        assert client.get(item.job_path).status_code == 200
        assert client.get(item.job_path + "/dispatches").status_code == 200
        response = client.get(item.download_path, params=item.params)
        assert response.status_code == 200 and response.content == DATA
        assert client.get(f'/api/publication-batches/{item.batch["id"]}/csv').status_code == 200
        assert apply(client, item.material.id, prepare(client, item.material.id, "RESTORE")).status_code == 200
        current = client.get(item.material_path).json()
        assert current["workflow_status"] == "IN_PROGRESS" and current["technical_identity"] == before["technical_identity"]
        metadata = client.get(item.material_path + "/metadata").json()
        assert metadata["current_snapshot_id"] is None and metadata["status"] == "NOT_SCANNED"
    assert len(item.worker.commands) == 1 and item.download.closed == 1


@pytest.mark.parametrize("restore", [False, True])
def test_inventory_observation_cannot_survive_archive_during_source_io(review_case, restore):
    case, worker, material_path = review_case; attach(case); identifier = case.materials[0].id
    def change_lifecycle():
        with case.client("ADMIN") as admin:
            assert apply(admin, identifier, prepare(admin, identifier)).status_code == 200
            if restore: assert apply(admin, identifier, prepare(admin, identifier, "RESTORE")).status_code == 200
    worker.callback = change_lifecycle
    with case.client("PROCESSOR") as reader:
        assert scan(reader, material_path, generation=0).status_code == (409 if restore else 404)
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(MaterialInventory)) == 0
        assert session.get(MaterialReviewState, identifier).inventory_id is None
    assert len(worker.calls) == 1


def test_history_has_bounded_ordered_pages_without_gaps(archive_case):
    case = archive_case; identifier = case.materials[0].id
    with case.client("ADMIN") as client:
        for version in range(1, 24):
            action = "ARCHIVE" if version % 2 else "RESTORE"
            assert apply(client, identifier, prepare(client, identifier, action)).status_code == 200
        first = client.get(path(identifier) + "/history").json()
        second = client.get(path(identifier) + "/history", params={"after": first["next_cursor"]}).json()
        assert [item["version"] for item in first["items"]] == list(range(23, 3, -1))
        assert [item["version"] for item in second["items"]] == [3, 2, 1] and second["next_cursor"] is None


def test_archive_listing_pages_preserve_identity_order_and_exclude_restored_rows(archive_case):
    from test_resource_history import creation
    case = archive_case; identifiers = []
    with case.client("ADMIN") as client:
        for number in range(22):
            created = client.post("/api/materials", json=creation(case, "MATERIAL") | {"material_name": f"Archived fixture {number}"})
            assert created.status_code == 201
            identifier = created.json()["id"]; identifiers.append(identifier)
            assert apply(client, identifier, prepare(client, identifier)).status_code == 200
        first = client.get("/api/material-archives").json()
        second = client.get("/api/material-archives", params={"after": first["next_cursor"]}).json()
        assert [value["material"]["id"] for value in first["items"] + second["items"]] == sorted(identifiers)
        assert len(first["items"]) == 20 and first["next_cursor"] == sorted(identifiers)[19]
        assert len(second["items"]) == 2 and second["next_cursor"] is None
        restored = sorted(identifiers)[-1]
        assert apply(client, restored, prepare(client, restored, "RESTORE")).status_code == 200
        assert [value["material"]["id"] for value in client.get("/api/material-archives", params={"after": first["next_cursor"]}).json()["items"]] == [sorted(identifiers)[-2]]


def test_recovery_is_bound_to_the_original_admin_and_target(archive_case):
    case = archive_case; identifier = case.materials[0].id
    with case.client("ADMIN") as admin:
        payload = prepare(admin, identifier)
        assert apply(admin, identifier, payload).status_code == 200
        assert admin.patch(f'/api/internal-users/{case.users["OTHER"].id}', json={"role": "ADMIN"}).status_code == 200
        assert admin.get(path(case.materials[1].id) + "/commands/" + payload["request_key"]).status_code == 404
    with case.client("OTHER") as other:
        assert other.get(path(identifier) + "/commands/" + payload["request_key"]).status_code == 404
        assert apply(other, identifier, payload).status_code == 409
        own = apply(other, case.materials[1].id, prepare(other, case.materials[1].id, key=UUID(payload["request_key"])))
        assert own.status_code == 200 and own.json()["event"]["actor_id"] == str(case.users["OTHER"].id)
