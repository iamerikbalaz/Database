import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.api import create_app
from app.identity_plan import plan_identity_change
from test_identity_execute import POSIX, contents, operation
from test_identity_plan import OLD, TARGET


def payload(case):
    return {"folder_path": "old-brand/" + OLD, "target_path": TARGET.path, "brand_name": TARGET.brand_name,
            "material_name": TARGET.material_name, "operation_id": case[4], "expected_plan_hash": case[3]["plan_hash"]}


@pytest.mark.parametrize("config", [{}, {"mutations_enabled": True}, {"mutations_enabled": True, "mutation_token": "too-short"}])
def test_write_route_fails_closed_without_complete_configuration(tmp_path, config):
    with TestClient(create_app(tmp_path, **config)) as client:
        response = client.post("/internal/material-identity-execute", json={"folder_path": "SAFE_0001_G03", "target_path": "NEXT_0001_G04",
            "brand_name": "Test", "material_name": "Test", "operation_id": str(uuid4()), "expected_plan_hash": "a" * 64})
        assert response.status_code == 503 and response.json()["detail"]["code"] == "SOURCE_MUTATIONS_DISABLED"
    assert not list(tmp_path.iterdir())


@POSIX
def test_execution_requires_service_auth_and_returns_no_private_source_bytes(operation):
    root, journals, original, _, _ = operation; token = uuid4().hex + uuid4().hex
    before = contents(original)
    with TestClient(create_app(root, mutations_enabled=True, mutation_token=token, journal_root=journals)) as client:
        response = client.post("/internal/material-identity-execute", json=payload(operation))
        assert response.status_code == 401 and contents(original) == before
        response = client.post("/internal/material-identity-execute", json=payload(operation), headers={"Authorization": "Bearer " + token})
        assert response.status_code == 200 and response.json()["status"] == "COMPLETED"
        assert "raw_content" not in response.text and "PRODUCT_NAME" not in response.text
        assert token not in response.text
        replay = client.post("/internal/material-identity-execute", json=payload(operation), headers={"Authorization": "Bearer " + token})
        assert replay.json() == response.json()


@POSIX
@pytest.mark.parametrize("change", ["uuid", "hash", "path", "target", "extra"])
def test_invalid_write_request_never_mutates_source_or_reflects_inputs(operation, change):
    root, journals, original, _, _ = operation; token = uuid4().hex
    value = payload(operation)
    if change == "uuid": value["operation_id"] = "SYNTHETIC_PRIVATE"
    if change == "hash": value["expected_plan_hash"] = "SYNTHETIC_PRIVATE"
    if change == "path": value["folder_path"] = "../SYNTHETIC_PRIVATE"
    if change == "target": value["target_path"] = "../SYNTHETIC_PRIVATE"
    if change == "extra": value["raw_metadata"] = "SYNTHETIC_PRIVATE"
    before = contents(original)
    with TestClient(create_app(root, mutations_enabled=True, mutation_token=token, journal_root=journals)) as client:
        response = client.post("/internal/material-identity-execute", json=value, headers={"Authorization": "Bearer " + token})
    assert response.status_code == 422 and "PRIVATE" not in response.text and contents(original) == before
    assert not list(journals.iterdir())


@POSIX
def test_plan_changed_between_confirmation_and_execution_is_durably_rejected(operation):
    root, journals, original, _, _ = operation; token = uuid4().hex
    (original / "new.txt").write_bytes(b"external change")
    before = contents(original)
    with TestClient(create_app(root, mutations_enabled=True, mutation_token=token, journal_root=journals)) as client:
        first = client.post("/internal/material-identity-execute", json=payload(operation), headers={"Authorization": "Bearer " + token})
        second = client.post("/internal/material-identity-execute", json=payload(operation), headers={"Authorization": "Bearer " + token})
    assert first.status_code == 200 and first.json()["status"] == "REJECTED" and first.json() == second.json()
    assert contents(original) == before
