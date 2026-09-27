"""Linked display names use the same audited source identity operation as folders."""
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.db.models import Company, MaterialIdentityHistory, PBRMaterial, PublishedBrand, ResourceChangeEvent, ResourceCommand
from test_application_access import access_case
from test_material_archives import apply, prepare


@pytest.mark.parametrize("role", ["ADMIN", "PRODUCTION_LEAD", "PROCESSOR"])
def test_linked_name_cannot_be_changed_by_generic_or_table_patch(access_case, role):
    case = access_case
    material_id = case.materials[0].id
    with case.database.session() as session:
        material = session.get(PBRMaterial, material_id)
        material.folder_path = "Library/" + material.technical_identity
        session.commit()
    with case.client(role) as client:
        path = f"/api/materials/{material_id}"
        before = client.get(path).json()
        rejected = client.patch(path, json={"material_name": "Unsynchronized name"}, headers={"Idempotency-Key": str(uuid4())})
        assert rejected.status_code == 409
        assert rejected.json()["detail"]["code"] == "IDENTITY_PLAN_REQUIRED"
        assert client.patch(path + "/table", json={"expected_updated_at": before["updated_at"], "material_name": "Unsynchronized name"},
            headers={"Idempotency-Key": str(uuid4())}).status_code == 422
        assert client.get(path).json() == before
    with case.database.session() as session:
        for model in (MaterialIdentityHistory, ResourceChangeEvent, ResourceCommand):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_unlinked_name_edits_and_linked_unchanged_name_remain_compatible(access_case):
    case = access_case
    material_id = case.materials[0].id
    with case.client("PROCESSOR") as client:
        path = f"/api/materials/{material_id}"
        response = client.patch(path, json={"material_name": "Updated before linking"})
        assert response.status_code == 200
        with case.database.session() as session:
            material = session.get(PBRMaterial, material_id)
            material.folder_path = "Library/" + material.technical_identity
            session.commit()
        current = client.get(path).json()
        assert client.patch(path, json={"material_name": current["material_name"]}).status_code == 200
        assert client.get(path).json()["material_name"] == current["material_name"]


@pytest.mark.parametrize("archived", [False, True])
def test_brand_name_cannot_desynchronize_active_or_archived_linked_materials(access_case, archived):
    case = access_case
    with case.database.session() as session:
        material = session.get(PBRMaterial, case.materials[0].id)
        material.folder_path = "Library/" + material.technical_identity
        brand_id = material.published_brand_id
        session.commit()
    with case.client("ADMIN") as client:
        if archived:
            assert apply(client, material.id, prepare(client, material.id)).status_code == 200
        path = f"/api/brands/{brand_id}"
        before = client.get(path).json()
        rejected = client.patch(path, json={"name": "Unsynchronized manufacturer"}, headers={"Idempotency-Key": str(uuid4())})
        assert rejected.status_code == 409
        assert rejected.json()["detail"]["code"] == "BRAND_SOURCE_REWRITE_REQUIRED"
        assert client.get(path).json() == before
        assert client.patch(path, json={"name": before["name"]}).status_code == 200


def test_company_rename_preserves_linked_brand_and_creates_same_name_brand(access_case):
    case = access_case
    with case.database.session() as session:
        material = session.get(PBRMaterial, case.materials[0].id)
        material.folder_path = "Library/" + material.technical_identity
        brand = session.get(PublishedBrand, material.published_brand_id)
        brand_id, company_id, original_brand_name = brand.id, brand.company_id, brand.name
        session.commit()
    with case.client("ADMIN") as client:
        result = client.patch(f"/api/companies/{company_id}", json={"name": "Renamed parent company"})
        assert result.status_code == 200
    with case.database.session() as session:
        assert session.get(Company, company_id).name == "Renamed parent company"
        assert session.get(PublishedBrand, brand_id).name == original_brand_name
        assert session.get(PBRMaterial, material.id).published_brand_id == brand_id
        replacement = session.scalar(select(PublishedBrand).where(PublishedBrand.company_id == company_id,
            PublishedBrand.name == "Renamed parent company"))
        assert replacement is not None and replacement.id != brand_id
