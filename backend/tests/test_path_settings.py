from uuid import uuid4
import pytest
from sqlalchemy import select
from app.db.path_settings_models import PathsSettingsRevision
from app.path_settings import PathSettingsError, resolve_sbs_template
from app.main import create_app
from test_application_access import access_case  # noqa: F401
from test_customer_orders import create_customer, create_order

ENDPOINT = "/api/settings/paths"


@pytest.fixture
def paths_case(access_case, tmp_path):
    paths = {key: tmp_path / key for key in ("templates", "orders", "materials", "published")}
    for path in paths.values(): path.mkdir()
    runtime = access_case.app.state.settings.model_copy(update={"sbs_templates_root": str(paths["templates"]), "order_folders_root": str(paths["orders"]), "materials_root": str(paths["materials"]), "published_library_root": str(paths["published"])})
    access_case.app = create_app(runtime, access_case.database, access_case.worker)
    return access_case, paths


def payload(paths, **changes):
    return {"idempotency_key": str(uuid4()), "expected_version": 0,
        "sbs_templates_root": str(paths["templates"]), "orders_root": str(paths["orders"]), "materials_root": str(paths["materials"]), "published_library_root": str(paths["published"]), **changes}


@pytest.mark.parametrize("role,status", [(None, 401), ("ADMIN", 200), ("PROCESSOR", 403), ("PRODUCTION_LEAD", 403), ("LEADERSHIP", 403)])
def test_paths_require_admin_for_read_and_write(paths_case, role, status):
    case, paths = paths_case
    with case.client(role) as client:
        assert client.get(ENDPOINT).status_code == status
        assert client.post(ENDPOINT, json=payload(paths)).status_code == status
        if role == "ADMIN":
            client.headers.pop("X-CSRF-Token")
            assert client.post(ENDPOINT, json=payload(paths, expected_version=1)).status_code == 403


def test_paths_saved_durably_with_version_exact_retry_and_immutable_history(paths_case):
    case, paths = paths_case
    request = payload(paths)
    with case.client("ADMIN") as client:
        assert client.get(ENDPOINT).json()["version"] == 0
        saved = client.post(ENDPOINT, json=request)
        assert saved.status_code == 200, saved.text
        assert saved.json()["version"] == 1
        assert client.post(ENDPOINT, json=request).json() == saved.json()
        assert client.post(ENDPOINT, json=payload(paths)).status_code == 409
        assert client.post(ENDPOINT, json={**request, "materials_root": str(paths["orders"])}).status_code == 409
    with case.client("ADMIN") as client:
        assert client.get(ENDPOINT).json() == saved.json()
    with case.database.session() as session:
        revision = session.scalar(select(PathsSettingsRevision))
        revision.request_hash = "f" * 64
        with pytest.raises(ValueError, match="append-only"): session.commit()


@pytest.mark.parametrize("field,value", [("orders_root", "relative"), ("sbs_templates_root", "missing"), ("materials_root", "outside"), ("orders_root", "overlap")])
def test_invalid_missing_outside_and_overlapping_paths_never_save(paths_case, field, value):
    case, paths = paths_case
    selected = "relative" if value == "relative" else str(paths["templates"].parent / "missing") if value == "missing" else str(paths["templates"]) if value == "outside" else str(paths["materials"])
    with case.client("ADMIN") as client:
        rejected = client.post(ENDPOINT, json=payload(paths, **{field: selected}))
        assert rejected.status_code == 422, rejected.text
        assert client.get(ENDPOINT).json()["version"] == 0


def test_pending_folder_operations_block_switching_order_root(paths_case, tmp_path):
    case, paths = paths_case
    another = tmp_path / "new-orders"; another.mkdir()
    with case.client("ADMIN") as client:
        create_order(client, create_customer(client))
        response = client.post(ENDPOINT, json=payload(paths, orders_root=str(another)))
        assert response.status_code == 409 and response.json()["detail"]["code"] == "ORDER_FOLDER_OPERATIONS_PENDING"


def test_saved_order_root_is_used_by_folder_dispatcher_for_new_orders(paths_case, tmp_path):
    from app.order_folders import process_next_folder_operation
    case, paths = paths_case
    another = tmp_path / "new-orders"; another.mkdir()
    with case.client("ADMIN") as client:
        assert client.post(ENDPOINT, json=payload(paths, orders_root=str(another))).status_code == 200
        order = create_order(client, create_customer(client))
        assert process_next_folder_operation(case.database, case.app.state.settings.model_copy(update={"order_folders_enabled": True}))
        saved = client.get("/api/orders/" + order["id"]).json()
        assert saved["folder_path"] == str(another / order["generated_name"])
        assert (another / order["generated_name"]).is_dir()
        assert not list(paths["orders"].iterdir())


def test_sbs_list_is_leaf_only_readable_files_and_traversal_is_rejected(paths_case):
    case, paths = paths_case
    (paths["templates"] / "Stone.sbs").write_text("template", encoding="utf-8")
    (paths["templates"] / "ignored.txt").write_text("ignored", encoding="utf-8")
    (paths["templates"] / "directory.sbs").mkdir()
    with case.client("PRODUCTION_LEAD") as client:
        response = client.get(ENDPOINT + "/sbs-templates")
        assert response.status_code == 200
        assert response.json() == {"items": [{"name": "Stone.sbs", "size_bytes": 8}]}
    for invalid in ("../Stone.sbs", "sub/Stone.sbs", "C:\\Stone.sbs", "Stone.sbs:stream", "ignored.txt"):
        with pytest.raises(PathSettingsError): resolve_sbs_template(str(paths["templates"]), invalid)


def test_sbs_links_are_not_eligible(paths_case):
    _, paths = paths_case
    actual = paths["templates"] / "actual.txt"; actual.write_text("outside-template")
    link = paths["templates"] / "link.sbs"
    try: link.symlink_to(actual)
    except OSError: pytest.skip("Symlinks unavailable on this Windows host")
    with pytest.raises(PathSettingsError): resolve_sbs_template(str(paths["templates"]), link.name)


def test_legacy_receipt_replays_without_rewriting_immutable_snapshot(paths_case):
    from app.material_review import canonical_hash
    case, paths = paths_case
    request = payload(paths)
    request.pop("published_library_root")
    snapshot = {key: request[key] for key in ("sbs_templates_root", "orders_root", "materials_root")}
    snapshot["version"] = 1
    from uuid import UUID
    with case.database.session() as session:
        session.add(PathsSettingsRevision(version=1, actor_id=case.users["ADMIN"].id,
            request_key=UUID(request["idempotency_key"]), request_hash=canonical_hash(request), response_snapshot=snapshot))
        session.commit()
    with case.client("ADMIN") as client:
        response = client.post(ENDPOINT, json=request)
        assert response.status_code == 200, response.text
        assert response.json() == {**snapshot, "published_library_root": str(paths["published"]), "can_select_folder": False}
        assert client.get(ENDPOINT).json() == response.json()
    with case.database.session() as session:
        assert session.scalar(select(PathsSettingsRevision)).response_snapshot == snapshot


def test_missing_published_target_does_not_block_saving_working_roots_or_expand_authority(paths_case, tmp_path):
    case, paths = paths_case
    missing = str(tmp_path / "offline-library" / "future")
    with case.client("ADMIN") as client:
        response = client.post(ENDPOINT, json=payload(paths, published_library_root=missing))
        assert response.status_code == 200, response.text
        assert response.json()["published_library_root"] == missing
        assert response.json()["materials_root"] == str(paths["materials"])
    assert case.app.state.settings.materials_root == str(paths["materials"])
    assert not (tmp_path / "offline-library").exists()


@pytest.mark.parametrize("field", ["templates", "orders", "materials"])
def test_published_library_cannot_overlap_other_roots_even_if_it_does_not_exist(paths_case, field):
    case, paths = paths_case
    with case.client("ADMIN") as client:
        response = client.post(ENDPOINT, json=payload(paths, published_library_root=str(paths[field] / "snapshots")))
        assert response.status_code == 422, response.text
        assert client.get(ENDPOINT).json()["version"] == 0


@pytest.mark.parametrize("value", ["relative", "C:\\", "C:\\folder\\..\\published", "C:\\published\x00"])
def test_published_library_still_requires_a_safe_absolute_path(paths_case, value):
    case, paths = paths_case
    with case.client("ADMIN") as client:
        assert client.post(ENDPOINT, json=payload(paths, published_library_root=value)).status_code == 422


def test_published_library_available_file_is_rejected(paths_case):
    case, paths = paths_case
    file = paths["published"] / "not-a-folder"; file.write_text("retained")
    with case.client("ADMIN") as client:
        assert client.post(ENDPOINT, json=payload(paths, published_library_root=str(file))).status_code == 422
    assert file.read_text() == "retained"


def test_old_runtime_and_saved_snapshot_receive_published_default_without_mutation():
    from types import SimpleNamespace
    from app.path_settings import paths_snapshot
    original = {"version": 1, "materials_root": "C:/Test_data"}
    result = paths_snapshot(original, SimpleNamespace())
    assert result["published_library_root"] == r"Z:\3. LIBRARY\3.3 PBR MATERIALS LIBRARY"
    assert "published_library_root" not in original
