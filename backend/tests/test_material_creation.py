from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, func

from app.db.models import PBRMaterial, Project, PublishedBrand, MaterialNumberReservation, OnlineCategory
from app.db.material_creation_models import MaterialCreationBatch
from app.api.material_creation import MaterialBatchCreate
from app.local_filesystem import LocalFilesError
from app.main import create_app
from app.material_review import canonical_hash
from test_application_access import access_case
from test_catalog_content import create_vocabulary


class CreatorStub:
    def __init__(self):
        self.created = []; self.captured = []; self.contexts = []; self.fail = set()
    def read_template(self, root, name):
        if name != "base.sbs": raise LocalFilesError("SBS_TEMPLATE_UNAVAILABLE")
        return b"synthetic graph"
    def capture_template(self, identifier, context, raw): self.captured.append((identifier, context, raw))
    def create_folder(self, identifier, item, context):
        if item["name"] in self.fail: raise LocalFilesError("MATERIAL_FOLDER_EXISTS")
        self.created.append(item["material_id"])
        self.contexts.append(context)
        return item["folder_path"]


@pytest.fixture
def creation_case(access_case, tmp_path, monkeypatch):
    case = access_case; creator = CreatorStub()
    monkeypatch.setattr("app.api.material_creation.LocalMaterialCreator", lambda library: creator)
    library = SimpleNamespace(fs=SimpleNamespace(root=tmp_path / "materials"))
    case.app = create_app(case.app.state.settings, case.database, case.worker, local_library=library)
    return case, creator


def payload(case, **changes):
    return {"idempotency_key": str(uuid4()), "expected_paths_version": 0,
        "published_brand_id": str(case.materials[0].published_brand_id), "assigned_processor_id": str(case.users["PROCESSOR"].id),
        "main_category_code": "G03", "names": ["New Orange", "New Yellow"], "template_name": "base.sbs", **changes}


def test_batch_reserves_unique_numbers_creates_optional_order_content_and_replays(creation_case):
    case, creator = creation_case
    with case.client("ADMIN") as client:
        category, collection = create_vocabulary(client, case.materials[0].published_brand_id)
        request = payload(case, category_ids=[category["id"]], collection_ids=[collection["id"]])
        first = client.post("/api/material-create-batches", json=request)
        assert first.status_code == 200, first.text
        result = first.json()
        assert result["status"] == "COMPLETED" and result["completed_count"] == 2
        assert [item["technical_identity"] for item in result["items"]] == ["SAFE_0003_NEW-ORANGE_G03", "SAFE_0004_NEW-YELLOW_G03"]
        assert client.post("/api/material-create-batches", json=request).json() == result
        assert len(creator.created) == 2 and len(creator.captured) == 1
        assert client.post("/api/material-create-batches", json={**request, "names": ["Different"]}).status_code == 409
        for item in result["items"]:
            material = client.get("/api/materials/" + item["material_id"]).json()
            assert material["project_id"] is None and material["folder_path"] == item["folder_path"]
            content = client.get("/api/materials/" + item["material_id"] + "/content").json()
            assert collection["id"] in [entry["id"] for entry in content["collections"]]
            assert category["id"] in [entry["id"] for entry in content["categories"]]
            assert content["required_category_id"] in [entry["id"] for entry in content["categories"]]
        assert client.get("/api/material-create-requests/" + request["idempotency_key"]).json() == result
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(MaterialNumberReservation)) == 2


def test_partial_folder_failure_resumes_same_records_without_duplicate_number_or_overwrite(creation_case):
    case, creator = creation_case; creator.fail.add("NEW-YELLOW")
    request = payload(case)
    with case.client("ADMIN") as client:
        first = client.post("/api/material-create-batches", json=request).json()
        assert first["status"] == "PARTIAL" and first["completed_count"] == 1
        assert first["items"][1]["error_code"] == "MATERIAL_FOLDER_EXISTS"
        creator.fail.clear()
        second = client.post("/api/material-create-batches", json=request).json()
        assert second["status"] == "COMPLETED"
        assert [item["material_id"] for item in second["items"]] == [item["material_id"] for item in first["items"]]
        assert creator.created == [item["material_id"] for item in first["items"]]
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(PBRMaterial)) == 4


@pytest.mark.parametrize("legacy_client", [False, True])
def test_new_batches_do_not_freeze_a_resolution_even_for_older_clients(creation_case, legacy_client):
    case, creator = creation_case
    request = payload(case, **({"resolution": 7} if legacy_client else {}))
    with case.client("ADMIN") as client:
        response = client.post("/api/material-create-batches", json=request)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "COMPLETED"
        assert client.post("/api/material-create-batches", json=request).json() == response.json()
    assert len(creator.captured) == 1 and len(creator.contexts) == 2
    assert all("resolution" not in context for context in creator.contexts)
    assert "resolution" not in creator.captured[0][1]
    with case.database.session() as session:
        batch = session.get(MaterialCreationBatch, UUID(response.json()["id"]))
        assert "resolution" not in batch.source_context


def test_resumes_a_legacy_batch_using_its_original_resolution_and_request_hash(creation_case):
    case, creator = creation_case
    material = case.materials[0]
    request = payload(case, names=[material.material_name], resolution=7)
    # This is the normalized request shape persisted by the prior version.
    old_payload = {**request, "project_id": None, "category_ids": [], "collection_ids": []}
    assert MaterialBatchCreate.model_validate(request).model_dump(mode="json") == old_payload
    context = {"materials_root": case.app.state.settings.materials_root, "paths_version": 0,
        "template_name": "base.sbs", "template_sha256": "a" * 64, "customer_folder": "SAFE", "resolution": 7}
    folder_path = "SAFE/" + material.technical_identity
    batch_id = uuid4()
    with case.database.session() as session:
        session.add(MaterialCreationBatch(id=batch_id, actor_id=case.users["ADMIN"].id,
            customer_id=material.published_brand_id, request_key=UUID(request["idempotency_key"]),
            request_hash=canonical_hash(old_payload), request_payload=old_payload, source_context=context,
            items=[{"material_id": str(material.id), "name": material.material_name,
                "technical_identity": material.technical_identity, "folder_path": folder_path,
                "status": "FAILED", "error_code": "MATERIAL_FOLDER_CREATE_FAILED"}], status="PARTIAL"))
        session.commit()
    with case.client("ADMIN") as client:
        changed = client.post("/api/material-create-batches", json={**request, "resolution": 8})
        assert changed.status_code == 409 and changed.json()["detail"]["code"] == "MATERIAL_CREATION_KEY_REUSED"
        recovered = client.post("/api/material-create-batches", json=request)
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["status"] == "COMPLETED"
        assert recovered.json()["id"] == str(batch_id)
        assert client.post("/api/material-create-batches", json=request).json() == recovered.json()
    assert creator.contexts == [context] and creator.created == [str(material.id)]
    assert not creator.captured
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(PBRMaterial)) == 2
        assert session.get(MaterialCreationBatch, batch_id).source_context == context


@pytest.mark.parametrize("role,expected", [(None,401),("LEADERSHIP",403),("PROCESSOR",403),("PRODUCTION_LEAD",200)])
def test_creation_access(creation_case, role, expected):
    case, creator = creation_case
    with case.client(role) as client:
        assert client.post("/api/material-create-batches", json=payload(case)).status_code == expected
    assert bool(creator.created) == (expected == 200)


@pytest.mark.parametrize("changes", [{"names":["A\tB"]},{"names":["Orange tiles","ORANGE-TILES"]},{"resolution":1.5},{"resolution":0},{"resolution":33},{"names":[]}])
def test_invalid_batch_does_not_allocate_or_touch_folders(creation_case, changes):
    case, creator = creation_case
    with case.client("ADMIN") as client:
        assert client.post("/api/material-create-batches", json=payload(case, **changes)).status_code == 422
    assert not creator.created and not creator.captured
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(PBRMaterial)) == 2


def test_order_customer_guard_create_and_assignment_only_keeps_historical_records(creation_case):
    case, creator = creation_case
    with case.database.session() as session:
        material = session.get(PBRMaterial, case.materials[0].id)
        brand = session.get(PublishedBrand, material.published_brand_id)
        other = PublishedBrand(company_id=brand.company_id, name="Other", folder_prefix="OTHER", brand_identifier="other")
        session.add(other); session.flush()
        order = session.get(Project, material.project_id); order.customer_id = other.id
        order_id = str(order.id); session.commit()
    with case.client("ADMIN") as client:
        response = client.post("/api/material-create-batches", json=payload(case, project_id=order_id))
        assert response.status_code == 409 and response.json()["detail"]["code"] == "MATERIAL_ORDER_CUSTOMER_MISMATCH"
        current = client.get("/api/materials/" + str(material.id)).json()
        note = client.patch("/api/materials/" + str(material.id) + "/table", headers={"Idempotency-Key": str(uuid4())},
            json={"expected_updated_at": current["updated_at"], "note": "Historical mismatch remains readable"})
        assert note.status_code == 200, note.text
        assert client.patch("/api/materials/" + str(material.id), json={"project_id": None}).status_code == 200
        assert client.patch("/api/materials/" + str(material.id), json={"project_id": order_id}).status_code == 409
    assert not creator.created


def test_creation_rejects_foreign_collections_before_public_folders(creation_case):
    case, creator = creation_case
    with case.client("ADMIN") as client:
        response = client.post("/api/material-create-batches", json=payload(case, collection_ids=[str(uuid4())]))
        assert response.status_code == 409
    assert not creator.created and not creator.captured


@pytest.mark.parametrize("multiple", [False, True])
def test_names_only_drafts_are_readable_and_replay_without_customer_numbers_or_io(creation_case, multiple):
    case, creator = creation_case
    request = {"idempotency_key": str(uuid4()), "names": ["Only Name", "Second Name"] if multiple else ["Only Name"]}
    with case.client("ADMIN") as client:
        response = client.post("/api/material-create-batches", json=request)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "COMPLETED" and result["completed_count"] == len(request["names"])
        assert client.post("/api/material-create-batches", json=request).json() == result
        for item in result["items"]:
            current = client.get("/api/materials/" + item["material_id"])
            assert current.status_code == 200, current.text
            material = current.json()
            assert material["is_draft"] is True
            for key in ("published_brand_id", "project_id", "assigned_processor_id", "main_category_code", "sequence_number", "technical_identity", "folder_path"):
                assert material[key] is None, (key, material)
            assert material["material_name"] == item["name"]
            content = client.get("/api/materials/" + item["material_id"] + "/content")
            assert content.status_code == 200, content.text
            assert content.json()["required_category_id"] is None and content.json()["categories"] == []
        listing = client.get("/api/materials")
        assert listing.status_code == 200 and len(listing.json()) == 2 + len(request["names"])
    assert not creator.created and not creator.captured
    with case.database.session() as session:
        assert session.get(PublishedBrand, case.materials[0].published_brand_id).next_sequence_number == 3
        assert session.scalar(select(func.count()).select_from(MaterialNumberReservation)) == 0


def test_names_only_without_desktop_or_templates(access_case):
    case = access_case
    with case.client("ADMIN") as client:
        options = client.get("/api/material-create-options")
        assert options.status_code == 200 and options.json()["can_create_folders"] is False
        response = client.post("/api/material-create-batches", json={"idempotency_key": str(uuid4()), "names": ["Offline Draft"]})
        assert response.status_code == 200, response.text
        assert response.json()["items"][0]["technical_identity"] is None


@pytest.mark.parametrize("first", ["published_brand_id", "main_category_code"])
def test_draft_properties_complete_identity_atomically_without_creating_folder(creation_case, first):
    case, creator = creation_case
    properties = {"published_brand_id": str(case.materials[0].published_brand_id), "main_category_code": "G03"}
    with case.client("ADMIN") as client:
        result = client.post("/api/material-create-batches", json={"idempotency_key": str(uuid4()), "names": ["Later Assigned"]}).json()
        identifier = result["items"][0]["material_id"]
        def update(field, value):
            material = client.get("/api/materials/" + identifier).json()
            request = {"expected_updated_at": material["updated_at"], field: value}
            key = str(uuid4())
            response = client.patch("/api/materials/" + identifier + "/table", json=request, headers={"Idempotency-Key": key})
            assert response.status_code == 200, response.text
            assert client.patch("/api/materials/" + identifier + "/table", json=request, headers={"Idempotency-Key": key}).json() == response.json()
            return response.json()
        first_result = update(first, properties[first])
        assert first_result["is_draft"] and first_result["sequence_number"] is None
        update("project_id", str(case.materials[0].project_id))
        second = next(field for field in properties if field != first)
        completed = update(second, properties[second])
        assert completed["is_draft"] is False and completed["sequence_number"] == 3
        assert completed["technical_identity"] == "SAFE_0003_LATER-ASSIGNED_G03"
        assert completed["folder_path"] is None and completed["assigned_processor_id"] is None
        update("assigned_processor_id", str(case.users["PROCESSOR"].id))
        update("assigned_processor_id", None)
        content = client.get("/api/materials/" + identifier + "/content").json()
        assert content["required_category_id"] is not None
    assert not creator.created and not creator.captured
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(MaterialNumberReservation)) == 1


def test_draft_rename_and_category_can_be_completed_on_record_without_identity_or_source_io(creation_case):
    case, creator = creation_case
    with case.client("ADMIN") as client:
        result = client.post("/api/material-create-batches", json={"idempotency_key": str(uuid4()), "names": ["First"]}).json()
        identifier = result["items"][0]["material_id"]
        response = client.patch("/api/materials/" + identifier, json={"material_name": "New Draft", "main_category_code": "G03", "assigned_processor_id": None})
        assert response.status_code == 200, response.text
        assert response.json()["material_name"] == "NEW-DRAFT" and response.json()["technical_identity"] is None
    assert not creator.created and not creator.captured


def test_draft_cannot_publish_complete_or_start_folder_io(creation_case):
    case, creator = creation_case
    with case.client("ADMIN") as client:
        result = client.post("/api/material-create-batches", json={"idempotency_key": str(uuid4()), "names": ["First"]}).json()
        identifier = result["items"][0]["material_id"]
        material = client.get("/api/materials/" + identifier).json()
        for field, value in (("is_published", True), ("workflow_status", "DONE")):
            response = client.patch("/api/materials/" + identifier + "/table", json={"expected_updated_at": material["updated_at"], field: value}, headers={"Idempotency-Key": str(uuid4())})
            assert response.status_code == 409, response.text
        assert client.post("/api/materials/" + identifier + "/folder-preflight", json={"folder_path": "SAFE/SAFE_0003_FIRST_G03"}).status_code == 409
        assert client.post("/api/materials/" + identifier + "/create-folder", json={"idempotency_key": str(uuid4()), "expected_updated_at": material["updated_at"]}).status_code == 409
    assert not creator.created and not creator.captured


def test_complete_identity_can_create_folder_without_processor_or_template(creation_case):
    case, creator = creation_case
    with case.client("ADMIN") as client:
        request = payload(case, assigned_processor_id=None, template_name=None)
        response = client.post("/api/material-create-batches", json=request)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "COMPLETED"
        assert client.post("/api/material-create-batches", json=request).json() == response.json()
    assert len(creator.created) == 2 and not creator.captured
    assert all(item["template_name"] is None for item in creator.contexts)


def test_existing_completed_draft_folder_request_replays_same_record_and_number(creation_case):
    case, creator = creation_case
    with case.client("ADMIN") as client:
        result = client.post("/api/material-create-batches", json={"idempotency_key": str(uuid4()), "names": ["Future Folder"], "published_brand_id": str(case.materials[0].published_brand_id)}).json()
        identifier = result["items"][0]["material_id"]
        response = client.patch("/api/materials/" + identifier, json={"main_category_code": "G03"})
        assert response.status_code == 200, response.text
        request = {"idempotency_key": str(uuid4()), "expected_updated_at": response.json()["updated_at"]}
        creator.fail.add("FUTURE-FOLDER")
        first = client.post("/api/materials/" + identifier + "/create-folder", json=request)
        assert first.status_code == 200 and first.json()["status"] == "PARTIAL", first.text
        competing = client.post("/api/materials/" + identifier + "/create-folder", json={**request, "idempotency_key": str(uuid4())})
        assert competing.status_code == 409, competing.text
        pending_edit = client.patch("/api/materials/" + identifier + "/table",
            json={"expected_updated_at": request["expected_updated_at"], "note": "Must not bypass pending creation"},
            headers={"Idempotency-Key": str(uuid4())})
        assert pending_edit.status_code == 409 and pending_edit.json()["detail"]["code"] == "MATERIAL_CREATION_ACTIVE"
        creator.fail.clear()
        completed = client.post("/api/materials/" + identifier + "/create-folder", json=request)
        assert completed.status_code == 200 and completed.json()["status"] == "COMPLETED", completed.text
        assert client.post("/api/materials/" + identifier + "/create-folder", json=request).json() == completed.json()
        assert completed.json()["items"][0]["material_id"] == identifier
        assert client.get("/api/materials/" + identifier).json()["sequence_number"] == 3
    assert creator.created == [identifier]
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(PBRMaterial)) == 3
        assert session.scalar(select(func.count()).select_from(MaterialNumberReservation)) == 1


def test_draft_completion_rechecks_category_retired_since_selection(creation_case):
    case, creator = creation_case
    with case.client("ADMIN") as client:
        draft = client.post("/api/material-create-batches", json={"idempotency_key": str(uuid4()),
            "names": ["Waiting For Customer"], "main_category_code": "G03"}).json()
        identifier = draft["items"][0]["material_id"]
        with case.database.session() as session:
            session.add(OnlineCategory(value="Facade / Tiles", normalized_key="facade / tiles", abbreviation="G03", is_active=False))
            session.commit()
        current = client.get("/api/materials/" + identifier).json()
        completed = client.patch("/api/materials/" + identifier + "/table",
            json={"expected_updated_at": current["updated_at"], "published_brand_id": str(case.materials[0].published_brand_id)},
            headers={"Idempotency-Key": str(uuid4())})
        assert completed.status_code == 422 and completed.json()["detail"]["code"] == "CATEGORY_INACTIVE"
        assert client.get("/api/materials/" + identifier).json()["sequence_number"] is None
    assert not creator.created and not creator.captured
    with case.database.session() as session:
        assert session.get(PublishedBrand, case.materials[0].published_brand_id).next_sequence_number == 3
        assert session.scalar(select(func.count()).select_from(MaterialNumberReservation)) == 0
