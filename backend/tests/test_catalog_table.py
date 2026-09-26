"""Catalog table edits retain provenance, optimistic versions and exact retries."""
from uuid import UUID, uuid4

from sqlalchemy import select

from app.db.models import CatalogAuditEvent
from test_application_access import access_case  # noqa: F401
from test_catalog_content import content_payload, create_vocabulary


def update(version=1, **change):
    return {"idempotency_key": str(uuid4()), "expected_version": version, "reason": "Test vocabulary correction", **change}


def test_catalog_table_exposes_dates_codes_and_replays_exact_version(access_case):
    with access_case.client("ADMIN") as client:
        category, _ = create_vocabulary(client, access_case.materials[0].published_brand_id)
        path = "/api/online-categories/" + category["id"] + "/table"
        payload = update(abbreviation="NAT_STONE")
        saved = client.patch(path, json=payload)
        assert saved.status_code == 200, saved.text
        assert saved.json()["abbreviation"] == "NAT_STONE" and saved.json()["version"] == 2
        assert saved.json()["created_at"]
        assert client.patch(path, json=payload).json() == saved.json()
        assert client.patch(path, json=update(abbreviation="NEW")).status_code == 409
        assert client.patch(path, json={**payload, "abbreviation": "DIFFERENT"}).status_code == 409
        assert client.get("/api/online-categories").json() == [saved.json()]
        cleared = client.patch(path, json=update(2, abbreviation=None))
        assert cleared.status_code == 200 and cleared.json()["abbreviation"] is None
        assert cleared.json()["version"] == 3
    with access_case.database.session() as session:
        events = list(session.scalars(select(CatalogAuditEvent).where(CatalogAuditEvent.resource_id == UUID(category["id"]))))
        assert len(events) == 3
        assert events[-1].result["audit"]["action"] == "ABBREVIATION_CHANGED"


def test_catalog_codes_are_unique_and_names_owner_and_type_stay_immutable(access_case):
    with access_case.client("ADMIN") as client:
        first = client.post("/api/online-categories", json={"idempotency_key": str(uuid4()), "value": "First", "abbreviation": "F01"})
        assert first.status_code == 201
        assert client.post("/api/online-categories", json={"idempotency_key": str(uuid4()), "value": "Second", "abbreviation": "F01"}).status_code == 409
        path = "/api/online-categories/" + first.json()["id"] + "/table"
        for change in ({"value": "Renamed"}, {"brand_id": str(uuid4())}, {"abbreviation": "no spaces allowed"},
                       {"is_active": None}, {"is_active": False, "abbreviation": "F02"}, {}):
            assert client.patch(path, json=update(**change)).status_code == 422
        no_change = client.patch(path, json=update(abbreviation="F01"))
        assert no_change.status_code == 200 and no_change.json()["version"] == 1


def test_catalog_table_invalidation_retains_exact_content_history(access_case):
    material = access_case.materials[0]
    with access_case.client("ADMIN") as client:
        category, collection = create_vocabulary(client, material.published_brand_id)
        content_path = f"/api/materials/{material.id}/content"
        saved = client.post(content_path, json=content_payload(credits=1, category_ids=[category["id"]], collection_ids=[collection["id"]])).json()
        history = client.get(content_path + "-history").json()
        before = client.get(f"/api/materials/{material.id}/review").json()
        response = client.patch("/api/collections/" + collection["id"] + "/table", json=update(abbreviation="ARCH"))
        assert response.status_code == 200, response.text
        assert client.get(f"/api/materials/{material.id}/review").json()["generation"] == before["generation"] + 1
        assert client.get(content_path + "-history").json() == history
        assert history[0]["snapshot"]["collections"] == saved["collections"]
        draft = client.get(content_path).json()
        assert "abbreviation" not in draft["collections"][0] and "created_at" not in draft["collections"][0]
    with access_case.client("PROCESSOR") as client:
        assert client.patch("/api/collections/" + collection["id"] + "/table", json=update(2, is_active=False)).status_code == 403


def test_old_create_hash_remains_replayable_without_new_optional_fields(access_case):
    from app.material_review import canonical_hash
    with access_case.client("ADMIN") as client:
        payload = {"idempotency_key": str(uuid4()), "value": "Legacy"}
        created = client.post("/api/online-categories", json=payload)
        assert created.status_code == 201
        with access_case.database.session() as session:
            event = session.scalar(select(CatalogAuditEvent))
            assert event.request_hash == canonical_hash({"kind": "CATEGORY", "id": None, "payload": payload})
        assert client.post("/api/online-categories", json=payload).json() == created.json()
