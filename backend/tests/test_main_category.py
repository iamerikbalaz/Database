"""Main category is a required material property in all content consumers."""
import json
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import func, select
from app.db.models import MaterialOnlineCategory, OnlineCategory, PBRMaterial
from app.main_category import CATEGORY_PATHS
from test_application_access import access_case
from test_catalog_content import content_payload
from test_material_identity import identity_case, prepare
from test_material_approvals import approval_case
from test_publication_preflight import prepare_candidate, preview


def test_backend_main_category_paths_match_the_card_catalog():
    source = Path(__file__).parents[2] / "frontend/src/data/materialCategories.json"
    if source.exists():
        assert CATEGORY_PATHS == {item["code"]: item["value"] for item in json.loads(source.read_text(encoding="utf-8"))}


def test_required_category_is_present_without_catalog_writes_and_cannot_be_removed(access_case):
    case = access_case; material = case.materials[0]; path = f"/api/materials/{material.id}/content"
    with case.client("ADMIN") as client:
        initial = client.get(path).json(); required = initial["categories"][0]
        assert initial["required_category_id"] == required["id"] and required["code"] == material.main_category_code
        assert required["source"] == "MAIN_CATEGORY" and required["catalog_id"] is None and required["is_required"]
        payload = content_payload(description="Draft", category_ids=[])
        saved = client.post(path, json=payload)
        assert saved.status_code == 200 and saved.json()["categories"] == [required]
        again = client.post(path, json=content_payload(expected_revision=1, description="Draft", category_ids=[required["id"]]))
        assert again.status_code == 200 and again.json()["revision"] == 1
        assert client.post(path, json=payload).json() == saved.json()
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(OnlineCategory)) == 0
        assert session.scalar(select(func.count()).select_from(MaterialOnlineCategory)) == 0


def test_unique_catalog_mapping_uses_actual_id_and_required_membership_is_not_optional(access_case):
    case = access_case; material = case.materials[0]; path = f"/api/materials/{material.id}"
    with case.client("ADMIN") as client:
        catalog = client.post("/api/online-categories", json={"idempotency_key": str(uuid4()), "value": "Main stone", "abbreviation": material.main_category_code}).json()
        initial = client.get(path + "/content").json()
        assert initial["required_category_id"] == catalog["id"] and initial["categories"][0]["catalog_id"] == catalog["id"]
        response = client.post(path + "/content", json=content_payload(description="Draft", category_ids=[catalog["id"]]))
        assert response.status_code == 200
        current = client.get(path).json()
        changed = client.patch(path + "/table", headers={"Idempotency-Key": str(uuid4())}, json={"expected_updated_at": current["updated_at"], "main_category_code": "G04"})
        assert changed.status_code == 200, changed.json()
        view = client.get(path + "/content").json()
        assert view["required_category_code"] == "G04" and view["categories"][0]["code"] == "G04"
        assert catalog["id"] not in [item["id"] for item in view["categories"]]


def test_controlled_identity_changes_required_category_without_editing_saved_history(identity_case):
    case, worker, path, target = identity_case
    with case.client("ADMIN") as client:
        assert client.post(path + "/content", json=content_payload(description="Draft")).status_code == 200
        history = client.get(path + "/content-history").json()
        saved = client.post(path + "/identity-confirm", json=prepare(client, path, target))
        assert saved.status_code == 200 and saved.json()["status"] == "COMPLETED"
        assert client.get(path + "/content").json()["required_category_code"] == target["main_category_code"]
        assert client.get(path + "/content-history").json() == history


def test_publication_preview_exports_the_same_required_category(approval_case):
    case, worker, path = approval_case
    material = prepare_candidate(case, worker, path)
    with case.client("ADMIN") as client:
        view = client.get(path + "/content").json()
        result = preview(client, material.id)
        assert result["can_prepare"]
        assert view["categories"][0]["value"] in result["items"][0]["row"]["categories"]
