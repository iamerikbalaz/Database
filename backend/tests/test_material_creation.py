from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, func

from app.db.models import PBRMaterial, Project, PublishedBrand, MaterialNumberReservation
from app.local_filesystem import LocalFilesError
from app.main import create_app
from test_application_access import access_case
from test_catalog_content import create_vocabulary


class CreatorStub:
    def __init__(self):
        self.created = []; self.captured = []; self.fail = set()
    def read_template(self, root, name):
        if name != "base.sbs": raise LocalFilesError("SBS_TEMPLATE_UNAVAILABLE")
        return b"synthetic graph"
    def capture_template(self, identifier, context, raw): self.captured.append((identifier, context, raw))
    def create_folder(self, identifier, item, context):
        if item["name"] in self.fail: raise LocalFilesError("MATERIAL_FOLDER_EXISTS")
        self.created.append(item["material_id"])
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
        "main_category_code": "G03", "names": ["New Orange", "New Yellow"], "resolution": 7, "template_name": "base.sbs", **changes}


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
