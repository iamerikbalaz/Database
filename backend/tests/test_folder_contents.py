import json
import httpx
import pytest

from app.db.models import InternalUser, PBRMaterial, UserCredential
from app.discovery_client import DiscoveryClientError
from app.folder_contents import FolderContents, WorkerFolderContentsClient
from app.main import create_app
from test_application_access import access_case
from test_material_operations import StreamingResponse


def payload(parent):
    return {"schema_version": 1, "parent_path": parent, "omitted_entries": 1, "entries": [
        {"name": "PREVIEW", "path": parent + "/PREVIEW", "kind": "directory", "size": 0},
        {"name": "metadata.txt", "path": parent + "/metadata.txt", "kind": "file", "size": 22}]}


class ContentsStub:
    def __init__(self): self.calls = []; self.callback = None; self.failure = None
    def listing(self, parent):
        self.calls.append(parent)
        if self.callback: self.callback()
        if self.failure: raise DiscoveryClientError(self.failure)
        return FolderContents.model_validate(payload(parent))


@pytest.fixture
def contents_case(access_case):
    case = access_case; worker = ContentsStub()
    case.app = create_app(case.app.state.settings, case.database, case.worker, folder_contents_client=worker)
    with case.database.session() as session:
        material = session.get(PBRMaterial, case.materials[0].id)
        material.folder_path = "Library/" + material.technical_identity
        session.commit()
    return case, worker, f"/api/materials/{material.id}/folder-contents", material.folder_path


@pytest.mark.parametrize("role,expected", [(None, 401), ("OTHER", 404), ("PROCESSOR", 200), ("LEADERSHIP", 200), ("PRODUCTION_LEAD", 200), ("ADMIN", 200)])
def test_scoped_material_contents_are_relative_and_readonly(contents_case, role, expected):
    case, worker, path, folder = contents_case
    with case.client(role) as client:
        response = client.get(path, params={"path": "16K"})
        assert response.status_code == expected and response.headers["cache-control"] == "no-store"
        if expected == 200:
            assert worker.calls == [folder + "/16K"]
            assert response.json()["entries"][1] == {"name": "metadata.txt", "path": "16K/metadata.txt", "kind": "file", "size": 22}
            assert folder not in response.text
        else: assert not worker.calls


@pytest.mark.parametrize("bad", ["../PRIVATE", "/PRIVATE", "C:/PRIVATE", "a\\PRIVATE", "a//b", "a\nPRIVATE", "/".join(["a"] * 17)])
def test_unsafe_relative_paths_do_not_reach_source(contents_case, bad):
    case, worker, path, _ = contents_case
    with case.client("ADMIN") as client:
        response = client.get(path, params={"path": bad})
        assert response.status_code == 422 and "PRIVATE" not in response.text
    assert not worker.calls


@pytest.mark.parametrize("failure", [None, "DISCOVERY_BUSY"])
@pytest.mark.parametrize("change,expected", [("disabled", 401), ("password", 403), ("assignment", 404), ("folder", 409)])
def test_contents_reauthorize_after_io_before_data_or_failure(contents_case, failure, change, expected):
    case, worker, path, _ = contents_case
    def during_io():
        with case.database.session() as session:
            if change == "disabled": session.get(InternalUser, case.users["PROCESSOR"].id).is_active = False
            elif change == "password": session.get(UserCredential, case.users["PROCESSOR"].id).must_change_password = True
            elif change == "assignment": session.get(PBRMaterial, case.materials[0].id).assigned_processor_id = case.users["OTHER"].id
            else: session.get(PBRMaterial, case.materials[0].id).folder_path = "changed/" + case.materials[0].technical_identity
            session.commit()
    worker.callback = during_io; worker.failure = failure
    with case.client("PROCESSOR") as client:
        response = client.get(path)
        assert response.status_code == expected and "metadata.txt" not in response.text and "DISCOVERY_BUSY" not in response.text


def test_contents_transport_binds_parent_and_disables_proxies(monkeypatch):
    def stream(method, url, **kwargs):
        assert method == "POST" and url == "http://worker/internal/folder-contents"
        assert kwargs["json"] == {"parent_path": "Library/TEST_0001_G01"}
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return StreamingResponse([json.dumps(payload(kwargs["json"]["parent_path"])).encode()])
    monkeypatch.setattr(httpx, "stream", stream)
    assert WorkerFolderContentsClient("http://worker").listing("Library/TEST_0001_G01").entries[1].size == 22


@pytest.mark.parametrize("defect", ["parent", "traversal", "name", "duplicate", "count", "unknown", "version", "size", "directory_size", "kind"])
def test_contents_untrusted_response_rejected_without_reflection(monkeypatch, defect):
    value = payload("Library")
    if defect == "parent": value["parent_path"] = "PRIVATE"
    elif defect == "traversal": value["entries"][0]["path"] = "Library/../PRIVATE"
    elif defect == "name": value["entries"][0]["name"] = "PRIVATE"
    elif defect == "duplicate": value["entries"].append(value["entries"][0])
    elif defect == "count": value["omitted_entries"] = 4096
    elif defect == "unknown": value["PRIVATE"] = "PRIVATE"
    elif defect == "version": value["schema_version"] = True
    elif defect == "size": value["entries"][1]["size"] = 64 * 1024**3 + 1
    elif defect == "directory_size": value["entries"][0]["size"] = 1
    else: value["entries"][0]["kind"] = "symlink"
    monkeypatch.setattr(httpx, "stream", lambda *_, **__: StreamingResponse([json.dumps(value).encode()]))
    with pytest.raises(DiscoveryClientError) as error: WorkerFolderContentsClient("http://worker").listing("Library")
    assert "PRIVATE" not in str(error.value) and error.value.__cause__ is None
