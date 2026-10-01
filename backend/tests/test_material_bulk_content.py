from uuid import UUID, uuid4
from app.db.models import PBRMaterial, PublishedBrand
from test_application_access import access_case
from test_catalog_content import create_vocabulary


def request(client, materials, category=None, collection=None):
    selected = []
    for item in materials:
        material = client.get(f"/api/materials/{item.id}").json()
        content = client.get(f"/api/materials/{item.id}/content").json()
        selected.append({"id": str(item.id), "expected_updated_at": material["updated_at"], "expected_revision": content["revision"]})
    return {"idempotency_key": str(uuid4()), "materials": selected,
        "category_ids": [category["id"]] if category else [], "collection_ids": [collection["id"]] if collection else []}


def test_bulk_is_additive_atomic_main_category_preserved_and_exact_replay(access_case):
    case = access_case
    with case.client("ADMIN") as client:
        category, collection = create_vocabulary(client, case.materials[0].published_brand_id)
        original = client.post(f"/api/materials/{case.materials[0].id}/content", json={"idempotency_key":str(uuid4()),"expected_revision":0,"description":"Keep prose","tags":["Keep tag"],"credits":7})
        assert original.status_code == 200
        body = request(client, case.materials, category, collection)
        saved = client.post("/api/material-content-batches", json=body)
        assert saved.status_code == 200 and saved.json()["updated_count"] == 2
        assert client.post("/api/material-content-batches", json=body).json() == saved.json()
        assert client.post("/api/material-content-batches", json={**body,"collection_ids":[]}).status_code == 409
        after = client.get(f"/api/materials/{case.materials[0].id}/content").json()
        assert after["description"] == "Keep prose" and after["credits"] == 7 and after["tags"] == ["Keep tag"]
        assert {after["required_category_id"],category["id"]} <= {item["id"] for item in after["categories"]}
        assert after["collections"][0]["id"] == collection["id"]


def test_stale_selection_rolls_back_all_selected_content(access_case):
    case = access_case
    with case.client("ADMIN") as client:
        category, _ = create_vocabulary(client, case.materials[0].published_brand_id)
        body = request(client, case.materials, category)
        body["materials"][1]["expected_revision"] = 100
        assert client.post("/api/material-content-batches", json=body).status_code == 409
        assert all(client.get(f"/api/materials/{item.id}/content").json()["revision"] == 0 for item in case.materials)


def test_bulk_collections_require_one_customer_but_categories_allow_multiple(access_case):
    case = access_case
    with case.database.session() as session:
        brand = session.get(PublishedBrand, case.materials[0].published_brand_id)
        other = PublishedBrand(company_id=brand.company_id,name="Second",folder_prefix="SECOND",brand_identifier="second")
        session.add(other); session.flush()
        session.get(PBRMaterial, case.materials[1].id).published_brand_id = other.id
        session.commit()
    with case.client("ADMIN") as client:
        category, collection = create_vocabulary(client, case.materials[0].published_brand_id)
        body = request(client, case.materials, category, collection)
        assert client.post("/api/material-content-batches", json=body).status_code == 409
        assert client.post("/api/material-content-batches", json={**body,"collection_ids":[]}).status_code == 200


def test_processor_cannot_include_someone_elses_material_and_csrf_is_required(access_case):
    case = access_case
    with case.client("ADMIN") as client:
        category, _ = create_vocabulary(client, case.materials[0].published_brand_id)
        body = request(client, case.materials, category)
    with case.client("PROCESSOR") as client:
        assert client.post("/api/material-content-batches", json=body).status_code == 404
        client.headers.pop("X-CSRF-Token")
        assert client.post("/api/material-content-batches", json=body).status_code == 403
