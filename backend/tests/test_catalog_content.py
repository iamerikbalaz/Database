"""Actual sessions, normalized individual values and immutable content revisions."""
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.catalog import ContentUpdate, CategoryCreate
from app.db.models import (BrandCollection, CatalogAuditEvent, MaterialContentRevision, MaterialReviewState,
    MaterialFileOperation, PBRMaterial, PublishedBrand)
from app.main import create_app
from test_application_access import access_case
from test_material_identity import IdentityStub, prepare


def create_vocabulary(client, brand_id):
    category = client.post("/api/online-categories", json={"idempotency_key": str(uuid4()), "value": "Natural stone"})
    assert category.status_code == 201
    collection = client.post("/api/collections", json={"idempotency_key": str(uuid4()), "value": "Architectural", "brand_id": str(brand_id)})
    assert collection.status_code == 201
    return category.json(), collection.json()


def content_payload(**updates):
    return {"idempotency_key": str(uuid4()), "expected_revision": 0, "reason": "Synthetic catalog correction", **updates}


@pytest.mark.parametrize("value", ["wood:stone", "", "   ", "hidden\u200bvalue", "two\nvalues", "null\x00", 12, "x" * 256])
def test_individual_catalog_values_reject_delimiters_controls_and_unbounded_input(value):
    with pytest.raises(ValidationError):
        CategoryCreate(idempotency_key=uuid4(), value=value)


def test_canonical_content_preserves_plain_text_and_deduplicates_individual_tags():
    data = ContentUpdate(**content_payload(description="  Café\r\nSecond line  ", tags=["  Cafe\u0301   stone  ", "CAFÉ STONE", "wood"]))
    assert data.description == "Café\nSecond line"
    assert data.tags == ["Café stone", "wood"]
    with pytest.raises(ValidationError):
        ContentUpdate(**content_payload(credits=True))
    with pytest.raises(ValidationError):
        ContentUpdate(**content_payload(tags=["a:b"]))


@pytest.mark.parametrize("role,allowed", [("PROCESSOR", False), ("LEADERSHIP", False), ("PRODUCTION_LEAD", True), ("ADMIN", True)])
def test_catalog_write_roles_and_read_access(access_case, role, allowed):
    with access_case.client(role) as client:
        assert client.get("/api/online-categories").status_code == 200
        assert client.get("/api/collections").status_code == 200
        response = client.post("/api/online-categories", json={"idempotency_key": str(uuid4()), "value": "Wood"})
        assert response.status_code == (201 if allowed else 403)
        assert client.get("/api/catalog-audit").status_code == (200 if allowed else 403)


def test_vocabulary_normalization_idempotency_history_and_immutable_names(access_case):
    with access_case.client("ADMIN") as client:
        body = {"idempotency_key": str(uuid4()), "value": "  Cafe\u0301   stone  "}
        created = client.post("/api/online-categories", json=body)
        assert created.status_code == 201 and created.json()["value"] == "Café stone"
        assert client.post("/api/online-categories", json=body).json() == created.json()
        assert client.post("/api/online-categories", json={**body, "value": "Other"}).status_code == 409
        assert client.post("/api/online-categories", json={"idempotency_key": str(uuid4()), "value": "CAFÉ STONE"}).status_code == 409
        path = "/api/online-categories/" + created.json()["id"]
        change = {"idempotency_key": str(uuid4()), "expected_version": 1, "is_active": False, "reason": "Retire synthetic label"}
        updated = client.patch(path, json=change)
        assert updated.status_code == 200 and updated.json()["version"] == 2
        assert client.patch(path, json=change).json() == updated.json()
        assert client.patch(path, json={**change, "idempotency_key": str(uuid4()), "is_active": True}).status_code == 409
        assert client.patch(path, json={**change, "value": "Renamed"}).status_code == 422
        assert len(client.get("/api/catalog-audit").json()) == 2
    with access_case.database.session() as session:
        event = session.scalar(select(CatalogAuditEvent)); event.resource_kind = "COLLECTION"
        with pytest.raises(Exception, match="append-only"):
            session.commit()


def test_content_normalized_relations_history_noop_and_published_invalidation(access_case):
    case = access_case; material = case.materials[0]; path = f"/api/materials/{material.id}/content"
    with case.database.session() as session:
        row = session.get(PBRMaterial, material.id); row.is_published = True
        session.commit()
    with case.client("ADMIN") as client:
        category, collection = create_vocabulary(client, material.published_brand_id)
        assert client.get(path).json()["content_status"] == "EMPTY"
        payload = content_payload(description="Synthetic description", credits=8, tags=["  matte ", "Matte", "stone"],
            category_ids=[category["id"], category["id"]], collection_ids=[collection["id"]])
        response = client.post(path, json=payload)
        assert response.status_code == 200, response.json()
        body = response.json()
        assert body["revision"] == 1 and body["tags"] == ["matte", "stone"]
        assert body["categories"] == [category] and body["collections"] == [collection]
        assert body["content_status"] == "MANUAL_DRAFT"
        assert client.post(path, json=payload).json() == body
        assert client.post(path, json={**payload, "reason": "Different request"}).status_code == 409
        before = client.get(f"/api/materials/{material.id}/review").json()
        assert before["generation"] == 1 and before["revision_hash"] is None
        assert client.post(path, json={**payload, "idempotency_key": str(uuid4()), "expected_revision": 1}).json() == body
        assert client.get(f"/api/materials/{material.id}/review").json() == before
        history = client.get(path + "-history").json()
        assert len(history) == 1 and history[0]["snapshot"]["tags"] == ["matte", "stone"]
        assert client.get(f"/api/materials/{material.id}").json()["publication_status"] == "PUBLISHED_UPDATE_REQUIRED"
        assert client.post(path, json=content_payload(credits=10)).status_code == 409
        assert client.post(path, json=content_payload(expected_revision=1)).json()["revision"] == 2
        assert len(client.get(path + "-history").json()) == 2
    with case.database.session() as session:
        revision = session.scalar(select(MaterialContentRevision)); revision.reason = "Changed"
        with pytest.raises(Exception, match="append-only"):
            session.commit()


@pytest.mark.parametrize("role,code", [("PROCESSOR", 200), ("OTHER", 404), ("LEADERSHIP", 403), ("PRODUCTION_LEAD", 200)])
def test_content_editor_roles_and_assignment(access_case, role, code):
    path = f"/api/materials/{access_case.materials[0].id}/content"
    with access_case.client(role) as client:
        assert client.get(path).status_code == (404 if role == "OTHER" else 200)
        assert client.get(path + "-history").status_code == (404 if role == "OTHER" else 200)
        assert client.post(path, json=content_payload(description="Synthetic draft")).status_code == code


def test_inactive_missing_or_other_brand_values_do_not_modify_content(access_case):
    case = access_case; material = case.materials[0]; path = f"/api/materials/{material.id}/content"
    with case.client("ADMIN") as client:
        category, collection = create_vocabulary(client, material.published_brand_id)
        with case.database.session() as session:
            brand = session.get(PublishedBrand, material.published_brand_id)
            other = PublishedBrand(company_id=brand.company_id, name="Other", folder_prefix="OTHER", brand_identifier="other")
            session.add(other); session.commit()
        other_collection = client.post("/api/collections", json={"idempotency_key": str(uuid4()), "value": "Other brand series", "brand_id": str(other.id)}).json()
        assert client.post(path, json=content_payload(collection_ids=[other_collection["id"]])).json()["detail"]["code"] == "CONTENT_COLLECTION_BRAND_MISMATCH"
        assert client.post(path, json=content_payload(category_ids=[str(uuid4())])).json()["detail"]["code"] == "CONTENT_CATALOG_VALUE_MISSING"
        assert client.patch("/api/online-categories/" + category["id"], json={"idempotency_key": str(uuid4()), "expected_version": 1,
            "is_active": False, "reason": "Retire synthetic value"}).status_code == 200
        assert client.post(path, json=content_payload(category_ids=[category["id"]])).json()["detail"]["code"] == "CONTENT_CATALOG_VALUE_INACTIVE"
        assert client.get(path).json()["revision"] == 0


def test_deactivation_invalidates_existing_material_review_without_erasing_membership(access_case):
    case = access_case; material = case.materials[0]; path = f"/api/materials/{material.id}"
    with case.client("ADMIN") as client:
        category, _ = create_vocabulary(client, material.published_brand_id)
        assert client.post(path + "/content", json=content_payload(category_ids=[category["id"]])).status_code == 200
        before = client.get(path + "/review").json()["generation"]
        response = client.patch("/api/online-categories/" + category["id"], json={"idempotency_key": str(uuid4()),
            "expected_version": 1, "is_active": False, "reason": "Retire synthetic value"})
        assert response.status_code == 200
        assert client.get(path + "/review").json()["generation"] == before + 1
        current = client.get(path + "/content").json()
        assert current["revision"] == 1 and current["categories"][0]["is_active"] is False
        assert client.post(path + "/content", json=content_payload(expected_revision=1)).status_code == 200


def test_identity_requires_explicit_removal_of_old_brand_collections_and_blocks_content_during_io(access_case):
    case = access_case; worker = IdentityStub(); material = case.materials[0]; path = f"/api/materials/{material.id}"
    case.app = create_app(case.app.state.settings.model_copy(update={"source_mutations_enabled": True}), case.database, case.worker, identity_client=worker)
    with case.database.session() as session:
        session.get(PBRMaterial, material.id).folder_path = "library/" + material.technical_identity
        old = session.get(PublishedBrand, material.published_brand_id)
        target = PublishedBrand(company_id=old.company_id, name="Target", folder_prefix="TARGET", brand_identifier="target")
        session.add(target); session.commit()
    with case.client("ADMIN") as client:
        category, collection = create_vocabulary(client, material.published_brand_id)
        assert client.post(path + "/content", json=content_payload(collection_ids=[collection["id"]])).status_code == 200
        request = {"target_brand_id": str(target.id), "main_category_code": "G04"}
        assert client.post(path + "/identity-plan", json=request).json()["detail"]["code"] == "IDENTITY_COLLECTIONS_ASSIGNED"
        assert not worker.plans
        assert client.post(path + "/content", json=content_payload(expected_revision=1, category_ids=[category["id"]])).status_code == 200
        payload = prepare(client, path, request)
        def during_source_io():
            assert client.post(path + "/content", json=content_payload(expected_revision=2, credits=5)).json()["detail"]["code"] == "MATERIAL_OPERATION_ACTIVE"
            assert client.patch("/api/online-categories/" + category["id"], json={"idempotency_key": str(uuid4()),
                "expected_version": 1, "is_active": False, "reason": "Synthetic change"}).status_code == 409
            current = client.get(path + "/content-review").json()
            assert client.post(path + "/content/approve", json={"idempotency_key": str(uuid4()),
                "expected_revision": current["content_revision"], "expected_context_hash": current["context_hash"]}).json()["detail"]["code"] == "MATERIAL_OPERATION_ACTIVE"
        worker.execute_callback = during_source_io
        assert client.post(path + "/identity-confirm", json=payload).json()["status"] == "COMPLETED"


def test_catalog_validation_does_not_echo_unknown_fields_or_submitted_content(access_case):
    with access_case.client("ADMIN") as client:
        marker = "PRIVATE_SYNTHETIC_MARKER"
        for path, body in (("/api/online-categories", {"idempotency_key": str(uuid4()), "value": marker + ":bad"}),
                           (f"/api/materials/{access_case.materials[0].id}/content", content_payload(**{marker: marker}))):
            response = client.post(path, json=body)
            assert response.status_code == 422 and marker not in response.text
