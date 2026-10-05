"""Confirmed customer rename, historical identity opt-in and real local journals."""
from datetime import UTC, datetime
import json
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.api.source_metadata import metadata_identity
from app.db.customer_rename_models import CustomerRenameOperation
from app.db.models import MaterialFileOperation, MaterialIdentityHistory, PBRMaterial, PublishedBrand
from app.main import create_app
from test_application_access import access_case  # noqa: F401
from test_customer_orders import write
from test_material_identity import IdentityStub


def prepare(client, customer_id, **changes):
    path = "/api/customers/" + str(customer_id)
    customer = client.get(path).json()
    body = {"name": "New customer", "rename_materials": False, "expected_updated_at": customer["updated_at"], **changes}
    plan = client.post(path + "/rename-plan", json=body)
    assert plan.status_code == 200, plan.text
    assert plan.json()["ready"], plan.text
    return path, {**body, "confirmed": True, "expected_proposal_hash": plan.json()["proposal_hash"]}


@pytest.mark.parametrize("rename_history", [False, True])
def test_customer_rename_leaves_drafts_incomplete_and_later_assigns_new_identity(access_case, rename_history):
    case = access_case
    brand_id = case.materials[0].published_brand_id
    with case.client("ADMIN") as client:
        created = client.post("/api/material-create-batches", json={"idempotency_key": str(uuid4()),
            "published_brand_id": str(brand_id), "names": ["Future Surface"]})
        assert created.status_code == 200, created.text
        identifier = created.json()["items"][0]["material_id"]
        path, body = prepare(client, brand_id, rename_materials=rename_history)
        renamed = write(client, "post", path + "/rename", body)
        assert renamed.status_code == 200, renamed.text
        draft = client.get("/api/materials/" + identifier).json()
        assert draft["is_draft"] and draft["technical_identity"] is None and draft["sequence_number"] is None
        completed = client.patch("/api/materials/" + identifier, json={"main_category_code": "G03"})
        assert completed.status_code == 200, completed.text
        assert completed.json()["technical_identity"] == "NEW-CUSTOMER_0003_FUTURE-SURFACE_G03"
        assert completed.json()["folder_path"] is None


def test_rename_future_only_keeps_historical_identity_metadata_and_counter(access_case):
    case = access_case
    with case.database.session() as session:
        old_brand = session.get(PublishedBrand, case.materials[0].published_brand_id)
        old_name, counter = old_brand.name, old_brand.next_sequence_number
        old = [(item.id, item.technical_identity, item.folder_path) for item in session.scalars(select(PBRMaterial).order_by(PBRMaterial.id))]
    with case.client("ADMIN") as client:
        path, body = prepare(client, old_brand.id)
        key = str(uuid4())
        result = write(client, "post", path + "/rename", body, key)
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "COMPLETED" and result.json()["total_count"] == 0
        assert result.json()["customer"]["folder_prefix"] == "NEW-CUSTOMER"
        assert write(client, "post", path + "/rename", body, key).json() == result.json()
        assert write(client, "post", path + "/rename", {**body, "name": "Different"}, key).status_code == 409
        next_material = write(client, "post", "/api/materials", {"project_id": str(case.materials[0].project_id),
            "published_brand_id": str(old_brand.id), "assigned_processor_id": str(case.users["PROCESSOR"].id),
            "material_name": "New product", "main_category_code": "G03"})
        assert next_material.status_code == 201, next_material.text
        assert next_material.json()["technical_identity"] == f"NEW-CUSTOMER_{counter:04d}_NEW-PRODUCT_G03"
    with case.database.session() as session:
        for identifier, identity, folder in old:
            item = session.get(PBRMaterial, identifier)
            assert (item.technical_identity, item.folder_path) == (identity, folder)
            assert metadata_identity(item)["MANUFACTURER"] == old_name
        assert session.scalar(select(func.count()).select_from(MaterialFileOperation)) == 0
        assert session.scalar(select(func.count()).select_from(CustomerRenameOperation)) == 1


def test_historical_rename_preserves_ids_numbers_and_unpublishes_only_changed_materials(access_case):
    case = access_case
    worker = IdentityStub()
    case.app = create_app(case.app.state.settings.model_copy(update={"source_mutations_enabled": True}), case.database, case.worker, identity_client=worker)
    with case.database.session() as session:
        for item in session.scalars(select(PBRMaterial)):
            item.folder_path = "library/" + item.technical_identity
            item.workflow_status = "DONE"
            item.is_published = True
        session.commit()
    with case.client("ADMIN") as client:
        path, body = prepare(client, case.materials[0].published_brand_id, rename_materials=True)
        response = write(client, "post", path + "/rename", body)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "COMPLETED"
        assert response.json()["completed_count"] == 2
        assert len(client.get(path + "/rename-operations").json()) == 1
    with case.database.session() as session:
        for previous in case.materials:
            item = session.get(PBRMaterial, previous.id)
            assert item.sequence_number == previous.sequence_number
            assert item.workflow_status == "DONE" and item.is_published is False
            assert item.technical_identity.startswith("NEW-CUSTOMER_")
            assert metadata_identity(item)["MANUFACTURER"] == "New customer"
        assert session.scalar(select(func.count()).select_from(MaterialIdentityHistory)) == 2
        assert session.get(PublishedBrand, previous.published_brand_id).next_sequence_number == 3


def test_explicit_confirmation_stale_revision_and_prefix_collision_are_required(access_case):
    case = access_case
    with case.client("ADMIN") as client:
        path, body = prepare(client, case.materials[0].published_brand_id)
        assert write(client, "post", path + "/rename", {**body, "confirmed": False}).status_code == 422
        assert write(client, "patch", path, {"expected_updated_at": body["expected_updated_at"], "name": "Unconfirmed"}).status_code == 409
        assert write(client, "patch", path, {"expected_updated_at": body["expected_updated_at"], "notes": "Concurrent change"}).status_code == 200
        assert write(client, "post", path + "/rename", body).status_code == 409
        other = write(client, "post", "/api/customers", {"name": "Collision", "folder_prefix": "TAKEN"}).json()
        current = client.get(path).json()
        result = client.post(path + "/rename-plan", json={"name": "Renamed", "folder_prefix": other["folder_prefix"], "expected_updated_at": current["updated_at"]})
        assert result.status_code == 409 and result.json()["detail"]["code"] == "CUSTOMER_PREFIX_USED"
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(CustomerRenameOperation)) == 0


def test_created_updated_date_ranges_include_end_date_and_reject_inversion(access_case):
    case = access_case
    identifier = case.materials[0].published_brand_id
    with case.database.session() as session:
        row = session.get(PublishedBrand, identifier)
        row.created_at = datetime(2025, 2, 1, 23, 59, 59, 999999, tzinfo=UTC)
        row.updated_at = datetime(2026, 3, 31, 23, 59, 59, 999999, tzinfo=UTC)
        session.commit()
    with case.client("ADMIN") as client:
        filters = {"created_from": "2025-02-01", "created_to": "2025-02-01", "updated_from": "2026-03-31", "updated_to": "2026-03-31"}
        assert [row["id"] for row in client.get("/api/customers", params=filters).json()] == [str(identifier)]
        assert client.get("/api/customers", params={**filters, "updated_to": "2026-03-30"}).status_code == 422
        assert client.get("/api/customers", params={"created_from": "2025-02-02"}).json() == []


@pytest.mark.skipif(os.name != "nt", reason="Real native filesystem journal")
@pytest.mark.parametrize("new_prefix", ["RENAMED", "SAFE"])
def test_local_customer_rename_updates_maps_and_metadata_in_place_or_new_folder(access_case, tmp_path, new_prefix):
    from app.local_materials import LocalMaterialLibrary
    case = access_case
    root = tmp_path / "library"
    old_identity = "SAFE_0001_MATERIAL-1_G03"
    source = root / "Brand" / old_identity
    (source / "4K").mkdir(parents=True)
    (source / "4K" / "SAFE_0001_MATERIAL-1_COL_4K.png").write_bytes(b"unchanged pixels")
    document = {"FOLDER": old_identity, "MANUFACTURER": "Synthetic brand", "PRODUCT_NUMBER": "0001",
                "PRODUCT_NAME": "Material 1", "CATEGORY": "G03", "BASE_NAME": "SAFE_0001_MATERIAL-1",
                "COLOR": {"hex": "#AABBCC"}, "TEXTURE_SIZE": {"cm": {"width": 12.5, "height": 25.2}}}
    (source / "metadata.json").write_text(json.dumps(document))
    library = LocalMaterialLibrary(root, tmp_path / "journal")
    case.app = create_app(case.app.state.settings.model_copy(update={"source_mutations_enabled": True}), case.database, case.worker, identity_client=library.identity)
    with case.database.session() as session:
        item = session.get(PBRMaterial, case.materials[0].id)
        item.technical_identity = old_identity
        item.folder_path = "Brand/" + old_identity
        item.is_published = True
        session.commit()
    with case.client("ADMIN") as client:
        path, body = prepare(client, case.materials[0].published_brand_id, name="Renamed brand", folder_prefix=new_prefix, rename_materials=True)
        response = write(client, "post", path + "/rename", body)
        assert response.status_code == 200 and response.json()["status"] == "COMPLETED", response.text
    target = root / "Brand" / f"{new_prefix}_0001_MATERIAL-1_G03"
    assert (target / "4K" / f"{new_prefix}_0001_MATERIAL-1_COL_4K.png").read_bytes() == b"unchanged pixels"
    after = json.loads((target / "metadata.json").read_text())
    assert after["MANUFACTURER"] == "Renamed brand" and after["FOLDER"] == target.name
    assert after["COLOR"] == document["COLOR"] and after["TEXTURE_SIZE"] == document["TEXTURE_SIZE"]
    assert source.exists() == (new_prefix == "SAFE")
