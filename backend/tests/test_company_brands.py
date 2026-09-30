from uuid import uuid4
from test_resources import client


def test_company_create_retry_rename_and_brand_transfer_keep_same_name_brand(client):
    key = str(uuid4())
    response = client.post("/api/companies", json={"name": "Alpha"}, headers={"Idempotency-Key": key})
    assert response.status_code == 201
    company = response.json()
    assert client.post("/api/companies", json={"name": "Alpha"}, headers={"Idempotency-Key": key}).json() == company
    brands = client.get("/api/brands").json()
    assert len(brands) == 1 and brands[0]["name"] == "Alpha" and brands[0]["company_id"] == company["id"]
    assert client.patch("/api/companies/" + company["id"], json={"name": "Renamed"}).status_code == 200
    same_name = [item for item in client.get("/api/brands").json() if item["name"] == "Renamed"]
    assert len(same_name) == 1
    refused = client.patch("/api/brands/" + same_name[0]["id"], json={"name": "Child brand"})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "CUSTOMER_RENAME_CONFIRMATION_REQUIRED"
    brands = client.get("/api/brands").json()
    assert len([item for item in brands if item["name"] == "Renamed"]) == 1
    assert next(item for item in brands if item["id"] == same_name[0]["id"])["name"] == "Renamed"


def test_duplicate_company_names_get_distinct_provisional_identifiers(client):
    for _ in range(2): assert client.post("/api/companies", json={"name": "Same name"}).status_code == 201
    brands = client.get("/api/brands").json()
    assert len(brands) == 2 and {item["name"] for item in brands} == {"Same name"}
    assert len({item["folder_prefix"] for item in brands}) == len({item["brand_identifier"] for item in brands}) == 2
