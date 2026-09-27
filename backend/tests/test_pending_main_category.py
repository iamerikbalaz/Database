"""Catalog remapping cannot strand the immutable request of a source save."""
from uuid import uuid4

import pytest

from app.main_category import CATEGORY_PATHS
from test_application_access import access_case
from test_source_metadata_edit import metadata_case, request_payload


@pytest.mark.parametrize("change", ["create_abbreviation", "create_canonical", "assign_abbreviation"])
def test_pending_combined_save_keeps_required_category_mapping_until_completion(metadata_case, change):
    case, worker, path = metadata_case
    worker.failed = True
    with case.client("ADMIN") as client:
        existing = None
        if change == "assign_abbreviation":
            created = client.post("/api/online-categories", json={"idempotency_key": str(uuid4()), "value": "Unmapped category"})
            assert created.status_code == 201
            existing = created.json()
        initial = client.get(path + "/content").json()
        original_category = initial["required_category_id"]
        assert initial["categories"][0]["catalog_id"] is None
        payload = {**request_payload(client, path), "content": {
            "idempotency_key": str(uuid4()), "expected_revision": initial["revision"],
            "description": "Pending source and content", "category_ids": [original_category], "collection_ids": []}}
        operation = client.post(path + "/source-metadata", json=payload)
        assert operation.status_code == 200 and operation.json()["status"] == "RUNNING"

        key = str(uuid4())
        code = case.materials[0].main_category_code
        if existing:
            endpoint = "/api/online-categories/" + existing["id"] + "/table"
            change_payload = {"idempotency_key": key, "expected_version": existing["version"], "abbreviation": code,
                "reason": "Assign a previously unused main category mapping"}
            rejected = client.patch(endpoint, json=change_payload)
        else:
            endpoint = "/api/online-categories"
            change_payload = {"idempotency_key": key,
                "value": CATEGORY_PATHS[code] if change == "create_canonical" else "New main category mapping"}
            if change == "create_abbreviation": change_payload["abbreviation"] = code
            rejected = client.post(endpoint, json=change_payload)
        assert rejected.status_code == 409
        assert rejected.json()["detail"]["code"] == "MATERIAL_OPERATION_ACTIVE"
        assert client.get(path + "/content").json()["required_category_id"] == original_category
        assert len(client.get("/api/online-categories").json()) == (1 if existing else 0)

        # Unrelated vocabulary remains editable while this material is owned.
        assert client.post("/api/online-categories", json={"idempotency_key": str(uuid4()), "value": "Unrelated optional category",
            "abbreviation": "OTHER"}).status_code == 201
        worker.failed = False
        resumed = client.post(path + "/source-metadata/" + operation.json()["id"] + "/resume")
        assert resumed.status_code == 200 and resumed.json()["status"] == "COMPLETED"
        assert client.get(path + "/content").json()["description"] == "Pending source and content"
        assert client.get(path + "/source-metadata").json()["active_operation"] is None

        # The rejected catalog transaction left no receipt or partial mutation;
        # its exact request can succeed after the durable source save completes.
        accepted = client.patch(endpoint, json=change_payload) if existing else client.post(endpoint, json=change_payload)
        assert accepted.status_code == (200 if existing else 201)
        assert client.get(path + "/content").json()["required_category_id"] == accepted.json()["id"]
