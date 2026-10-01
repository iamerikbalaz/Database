"""Explicit catalog corrections keep material identity and audit snapshots intact."""
from uuid import UUID, uuid4

from sqlalchemy import select

from app.db.models import CatalogAuditEvent, OnlineCategory, PBRMaterial
from test_application_access import access_case  # noqa: F401
from test_catalog_content import content_payload
from test_catalog_table import update
from test_material_creation import creation_case, payload as creation_payload  # noqa: F401


def test_catalog_identity_edit_is_versioned_replayable_and_does_not_rename_materials(access_case):
    material = access_case.materials[0]
    with access_case.client("ADMIN") as client:
        category = client.post("/api/online-categories", json={"idempotency_key": str(uuid4()), "value": "Original", "abbreviation": material.main_category_code}).json()
        content_path = f"/api/materials/{material.id}/content"
        assert client.post(content_path, json=content_payload(description="Original snapshot")).status_code == 200
        history = client.get(content_path + "-history").json()
        with access_case.database.session() as session:
            row = session.get(PBRMaterial, material.id)
            identity = (row.main_category_code, row.technical_identity, row.folder_path)
        before = client.get(f"/api/materials/{material.id}/review").json()
        path = f"/api/online-categories/{category['id']}/identity"
        payload = update(value="Updated category", abbreviation="NEW01")
        response = client.patch(path, json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["value"] == "Updated category" and response.json()["abbreviation"] == "NEW01"
        assert response.json()["version"] == 2
        assert client.patch(path, json=payload).json() == response.json()
        assert client.patch(path, json=update(value="Stale", abbreviation="NEW02")).status_code == 409
        assert client.get(content_path + "-history").json() == history
        assert client.get(f"/api/materials/{material.id}/review").json()["generation"] == before["generation"] + 1
        with access_case.database.session() as session:
            row = session.get(PBRMaterial, material.id)
            assert (row.main_category_code, row.technical_identity, row.folder_path) == identity
            catalog = session.get(OnlineCategory, UUID(category["id"]))
            assert catalog.normalized_key == "updated category"
            events = list(session.scalars(select(CatalogAuditEvent).where(CatalogAuditEvent.resource_id == catalog.id)))
            assert len(events) == 2
            audit = events[-1].result["audit"]
            assert audit["action"] == "IDENTITY_CHANGED"
            assert audit["before"] == {"value": "Original", "abbreviation": material.main_category_code}
            assert audit["after"] == {"value": "Updated category", "abbreviation": "NEW01"}


def test_catalog_identity_rejects_duplicate_codes_names_and_unauthorized_edits(access_case):
    with access_case.client("ADMIN") as client:
        first = client.post("/api/online-categories", json={"idempotency_key": str(uuid4()), "value": "First", "abbreviation": "A01"}).json()
        second = client.post("/api/online-categories", json={"idempotency_key": str(uuid4()), "value": "Second", "abbreviation": "B01"}).json()
        path = f"/api/online-categories/{second['id']}/identity"
        assert client.patch(path, json=update(value="Other", abbreviation="A01")).status_code == 409
        assert client.patch(path, json=update(value="FIRST", abbreviation="C01")).status_code == 409
        rows = client.get("/api/online-categories").json()
        assert next(row for row in rows if row["id"] == second["id"])["version"] == 1
        assert client.patch(path, json=update(value="Second", abbreviation="B01")).json()["version"] == 1
        assert client.patch(path, json={**update(value="Other", abbreviation="C01"), "brand_id": first["id"]}).status_code == 422
    with access_case.client("PROCESSOR") as client:
        assert client.patch(path, json=update(value="Other", abbreviation="C01")).status_code == 403


def test_collection_identity_cannot_change_owner_and_retries_remain_exact(access_case):
    with access_case.client("ADMIN") as client:
        created = client.post("/api/collections", json={"idempotency_key": str(uuid4()), "value": "First", "brand_id": str(access_case.materials[0].published_brand_id), "abbreviation": "COL"}).json()
        path = f"/api/collections/{created['id']}/identity"
        payload = update(value="New collection", abbreviation="COL2")
        saved = client.patch(path, json=payload)
        assert saved.status_code == 200, saved.text
        assert saved.json()["brand_id"] == created["brand_id"]
        assert client.patch(path, json=payload).json() == saved.json()
        assert client.patch(path, json={**payload, "value": "Different"}).status_code == 409


def test_historical_main_code_stays_attached_and_cannot_be_reassigned(access_case):
    material = access_case.materials[0]
    with access_case.client("ADMIN") as client:
        category = client.post("/api/online-categories", json={"idempotency_key": str(uuid4()), "value": "Original", "abbreviation": "G03"}).json()
        path = f"/api/online-categories/{category['id']}/identity"
        original = client.get(f"/api/materials/{material.id}").json()
        assert client.post(f"/api/materials/{material.id}/content", json=content_payload(description="Saved before correction")).status_code == 200
        assert client.patch(path, json=update(value="New label", abbreviation="NEXT")).status_code == 200
        content = client.get(f"/api/materials/{material.id}/content").json()
        assert content["required_category_id"] == category["id"]
        assert content["required_category_code"] == "G03"
        assert content["categories"][0]["value"] == "New label"
        assert content["categories"][0]["is_required"] is True
        before = client.get(f"/api/materials/{material.id}/review").json()["generation"]
        again = client.patch(path, json=update(2, value="Final label", abbreviation="LATEST"))
        assert again.status_code == 200 and set(again.json()["aliases"]) == {"G03", "NEXT", "LATEST"}
        assert client.get(f"/api/materials/{material.id}/review").json()["generation"] == before + 1
        assert client.get(f"/api/materials/{material.id}/content").json()["categories"][0]["value"] == "Final label"
        assert client.post("/api/online-categories", json={"idempotency_key": str(uuid4()), "value": "Unrelated", "abbreviation": "G03"}).status_code == 409
        assert client.post("/api/online-categories", json={"idempotency_key": str(uuid4()), "value": "Unrelated", "abbreviation": "NEXT"}).status_code == 409
        create = {"published_brand_id": str(material.published_brand_id), "material_name": "New material", "main_category_code": "G03", "assigned_processor_id": str(material.assigned_processor_id)}
        retired = client.post("/api/materials", json=create)
        assert retired.status_code == 422 and retired.json()["detail"]["code"] == "CATEGORY_CODE_RETIRED"
        added = client.post("/api/materials", json={**create, "main_category_code": "LATEST"})
        assert added.status_code == 201, added.text
        results = client.get("/api/materials", params={"main_category_code": "LATEST"}).json()
        assert {original["id"], added.json()["id"]} <= {row["id"] for row in results}
        customers = client.get("/api/customers", params={"main_category_code": "LATEST"}).json()
        assert str(material.published_brand_id) in {row["id"] for row in customers}
        current = client.get(f"/api/materials/{material.id}").json()
        assert all(current[field] == original[field] for field in ("main_category_code", "technical_identity", "folder_path"))
        assert client.patch(f"/api/online-categories/{category['id']}", json=update(3, is_active=False)).status_code == 200
        inactive = client.post("/api/materials", json={**create, "main_category_code": "LATEST"})
        assert inactive.status_code == 422 and inactive.json()["detail"]["code"] == "CATEGORY_INACTIVE"
        assert client.patch(f"/api/materials/{material.id}", json={"main_category_code": "G03"}).status_code == 200


def test_canonical_name_without_explicit_code_retains_required_association_after_edit(access_case):
    material = access_case.materials[0]
    with access_case.client("ADMIN") as client:
        category = client.post("/api/online-categories", json={"idempotency_key": str(uuid4()), "value": "Facade / Tiles"}).json()
        assert client.patch(f"/api/online-categories/{category['id']}/identity", json=update(value="Facade tiles revised", abbreviation="TILES")).status_code == 200
        content = client.get(f"/api/materials/{material.id}/content").json()
        assert content["required_category_id"] == category["id"]
        assert content["categories"][0]["value"] == "Facade tiles revised"


def test_batch_creation_rejects_retired_and_inactive_codes_but_keeps_exact_replay(creation_case):
    case, creator = creation_case
    with case.client("ADMIN") as client:
        category = client.post("/api/online-categories", json={"idempotency_key": str(uuid4()), "value": "Batch category", "abbreviation": "G03"}).json()
        request = creation_payload(case)
        first = client.post("/api/material-create-batches", json=request)
        assert first.status_code == 200, first.text
        assert client.patch(f"/api/online-categories/{category['id']}/identity", json=update(value="Renamed batch category", abbreviation="BATCH")).status_code == 200
        assert client.post("/api/material-create-batches", json=request).json() == first.json()
        retired = client.post("/api/material-create-batches", json=creation_payload(case))
        assert retired.status_code == 422 and retired.json()["detail"]["code"] == "CATEGORY_CODE_RETIRED"
        assert client.patch(f"/api/online-categories/{category['id']}", json=update(2, is_active=False)).status_code == 200
        inactive = client.post("/api/material-create-batches", json=creation_payload(case, main_category_code="BATCH"))
        assert inactive.status_code == 422 and inactive.json()["detail"]["code"] == "CATEGORY_INACTIVE"
        assert len(creator.created) == 2 and len(creator.captured) == 1
