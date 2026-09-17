import json

import httpx
import pytest

from app.db.models import InternalUser, PBRMaterial, UserCredential
from app.discovery_client import DiscoveryClientError, FolderDiscovery, WorkerDiscoveryClient
from app.main import create_app
from test_application_access import access_case
from test_material_operations import StreamingResponse


def payload(parent="library", name="SAFE_0001_G03"):
    return {"schema_version": 1, "parent_path": parent, "omitted_entries": 2,
        "directories": [{"name": name, "path": (parent + "/" if parent else "") + name}]}


class DiscoveryStub:
    def __init__(self, identity): self.identity = identity; self.calls = []; self.callback = None; self.failure = None
    def listing(self, parent):
        self.calls.append(parent)
        if self.callback: self.callback()
        if self.failure: raise DiscoveryClientError(self.failure)
        return FolderDiscovery.model_validate(payload(parent, self.identity))


@pytest.fixture
def discovery_case(access_case):
    case = access_case; worker = DiscoveryStub(case.materials[0].technical_identity)
    case.app = create_app(case.app.state.settings, case.database, case.worker, discovery_client=worker)
    return case, worker, f"/api/materials/{case.materials[0].id}/folder-discovery"


@pytest.mark.parametrize("role,expected", [(None, 401), ("PROCESSOR", 403), ("OTHER", 403), ("PRODUCTION_LEAD", 403), ("LEADERSHIP", 403), ("ADMIN", 200)])
def test_only_administrator_can_browse_and_nothing_is_linked(discovery_case, role, expected):
    case, worker, path = discovery_case
    with case.client(role) as client:
        result = client.post(path, json={"parent_path": "library"})
        assert result.status_code == expected and result.headers["cache-control"] == "no-store"
        if expected == 200:
            assert result.json()["directories"][0]["identity_matches"] is True
            assert result.json()["material_id"] == str(case.materials[0].id)
        else: assert worker.calls == []
    with case.database.session() as session:
        assert session.get(PBRMaterial, case.materials[0].id).folder_path is None


@pytest.mark.parametrize("failure", [None, "DISCOVERY_BUSY"])
@pytest.mark.parametrize("change,expected", [("role", 403), ("disable", 401), ("password", 403), ("material", 409)])
def test_reauthorize_before_source_data_or_error_is_disclosed(discovery_case, change, expected, failure):
    case, worker, path = discovery_case
    def during_io():
        with case.database.session() as session:
            if change == "role": session.get(InternalUser, case.users["ADMIN"].id).role = "LEADERSHIP"
            elif change == "disable": session.get(InternalUser, case.users["ADMIN"].id).is_active = False
            elif change == "password": session.get(UserCredential, case.users["ADMIN"].id).must_change_password = True
            else: session.get(PBRMaterial, case.materials[0].id).folder_path = "changed/" + worker.identity
            session.commit()
    worker.callback = during_io; worker.failure = failure
    with case.client("ADMIN") as client:
        result = client.post(path, json={"parent_path": "library"})
        assert result.status_code == expected and worker.identity not in result.text and "DISCOVERY_BUSY" not in result.text


def test_invalid_paths_and_schema_do_not_reach_worker_or_reflect_input(discovery_case):
    case, worker, path = discovery_case
    with case.client("ADMIN") as client:
        for parent in ("../PRIVATE_SYNTHETIC", "/PRIVATE_SYNTHETIC", "a//b", "a\\b", "a\nPRIVATE_SYNTHETIC", "é" * 128, "/".join(["a"] * 17)):
            response = client.post(path, json={"parent_path": parent})
            assert response.status_code == 422 and "PRIVATE_SYNTHETIC" not in response.text
        response = client.post(path, json={"parent_path": {"PRIVATE_SYNTHETIC": "PRIVATE_SYNTHETIC"}})
        assert response.status_code == 422 and "PRIVATE_SYNTHETIC" not in response.text
        assert worker.calls == []


def test_discovery_requires_csrf_and_allowed_origin(discovery_case):
    case, worker, path = discovery_case
    with case.client("ADMIN") as client:
        client.headers.pop("X-CSRF-Token", None)
        assert client.post(path, json={"parent_path": ""}).status_code == 403
    with case.client("ADMIN") as client:
        assert client.post(path, json={"parent_path": ""}, headers={"Origin": "https://untrusted.invalid"}).status_code == 403
    assert worker.calls == []


def test_transport_binds_parent_and_disables_redirects_and_environment(monkeypatch):
    def stream(method, url, **kwargs):
        assert method == "POST" and url == "http://worker/internal/folder-discovery"
        assert kwargs["json"] == {"parent_path": ""} and kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return StreamingResponse([json.dumps(payload("")).encode()])
    monkeypatch.setattr(httpx, "stream", stream)
    assert WorkerDiscoveryClient("http://worker").listing("").directories[0].name == "SAFE_0001_G03"


@pytest.mark.parametrize("defect", ["parent", "traversal", "name", "duplicate", "count", "unknown", "version", "length", "depth"])
def test_untrusted_worker_results_are_strictly_validated(monkeypatch, defect):
    value = payload()
    if defect == "parent": value["parent_path"] = "other"
    elif defect == "traversal": value["directories"][0]["path"] = "library/../PRIVATE_SYNTHETIC"
    elif defect == "name": value["directories"][0]["name"] = "other"
    elif defect == "duplicate": value["directories"].append(value["directories"][0])
    elif defect == "count": value["omitted_entries"] = 4096
    elif defect == "unknown": value["PRIVATE_SYNTHETIC"] = "PRIVATE_SYNTHETIC"
    elif defect == "version": value["schema_version"] = True
    elif defect == "length": value["directories"][0] = {"name": "é" * 128, "path": "library/" + "é" * 128}
    else: value["parent_path"] = "/".join(["a"] * 17)
    monkeypatch.setattr(httpx, "stream", lambda *_, **__: StreamingResponse([json.dumps(value).encode()]))
    with pytest.raises(DiscoveryClientError) as error: WorkerDiscoveryClient("http://worker").listing("library")
    assert "PRIVATE_SYNTHETIC" not in str(error.value) and error.value.__cause__ is None


@pytest.mark.parametrize("declared", [True, False])
def test_transport_bounds_actual_and_declared_lengths(monkeypatch, declared):
    monkeypatch.setattr("app.discovery_client.MAX_RESPONSE_BYTES", 10)
    response = StreamingResponse([b"x" * 11], content_length=11 if declared else 1)
    monkeypatch.setattr(httpx, "stream", lambda *_, **__: response)
    with pytest.raises(DiscoveryClientError): WorkerDiscoveryClient("http://worker").listing("")
    assert response.closed


def test_duplicate_json_fields_are_rejected(monkeypatch):
    raw = json.dumps(payload()).replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')
    monkeypatch.setattr(httpx, "stream", lambda *_, **__: StreamingResponse([raw.encode()]))
    with pytest.raises(DiscoveryClientError): WorkerDiscoveryClient("http://worker").listing("library")
