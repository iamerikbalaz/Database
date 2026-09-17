import base64
import hashlib
import json
from uuid import uuid4

import httpx
import pytest

from app.db.models import InternalUser, PBRMaterial, UserCredential
from app.main import create_app
from app.preview_client import PreviewClientError, PreviewImage, PreviewListing, WorkerPreviewClient
from test_application_access import access_case
from test_material_operations import StreamingResponse


# Transport-only fixture. Actual JPEG decoding and metadata removal are exercised
# by worker/tests/test_previews.py on Linux; this mock never enters production.
PIXELS = b"\xff\xd8\xff\xe0synthetic-transport-fixture\xff\xd9"


def listing_payload(identity="SAFE_0001_G03"):
    return {"schema_version": 1, "folder_name": identity, "missing": False, "ignored_entries": 0,
        "items": [{"name": "Synthetic preview.png", "size": 20, "sha256": "a" * 64}]}


def image_payload(identity="SAFE_0001_G03"):
    return {"schema_version": 1, "folder_name": identity, "name": "Synthetic preview.png", "source_sha256": "a" * 64,
        "source_format": "PNG", "width": 80, "height": 40, "media_type": "image/jpeg",
        "sha256": hashlib.sha256(PIXELS).hexdigest(), "data": base64.b64encode(PIXELS).decode()}


class PreviewStub:
    def __init__(self): self.calls = []; self.callback = None; self.failure = None
    def _read(self, folder):
        self.calls.append(folder)
        if self.callback: self.callback()
        if self.failure: raise PreviewClientError(self.failure)
    def listing(self, folder):
        self._read(folder)
        return PreviewListing.model_validate_json(json.dumps(listing_payload(folder.rsplit("/", 1)[-1])))
    def image(self, folder, name, expected_sha256):
        self._read(folder)
        return PreviewImage.model_validate_json(json.dumps(image_payload(folder.rsplit("/", 1)[-1])))


@pytest.fixture
def preview_case(access_case):
    case = access_case; worker = PreviewStub()
    with case.database.session() as session:
        for material in case.materials:
            session.get(PBRMaterial, material.id).folder_path = "library/" + material.technical_identity
        session.commit()
    case.app = create_app(case.app.state.settings, case.database, case.worker, preview_client=worker)
    return case, worker, f"/api/materials/{case.materials[0].id}"


@pytest.mark.parametrize("role,code", [(None, 401), ("PROCESSOR", 200), ("OTHER", 404), ("LEADERSHIP", 200), ("PRODUCTION_LEAD", 200), ("ADMIN", 200)])
def test_preview_reads_use_actual_sessions_and_assignment(preview_case, role, code):
    case, worker, path = preview_case
    with case.client(role) as client:
        response = client.get(path + "/previews")
        assert response.status_code == code
        image = client.get(path + "/preview", params={"name": "Synthetic preview.png", "expected_sha256": "a" * 64})
        assert image.status_code == code
        if code == 200:
            assert response.json()["material_id"] == str(case.materials[0].id)
            assert "folder_name" not in response.json()
            assert image.content == PIXELS and image.headers["content-type"] == "image/jpeg"
            assert image.headers["cache-control"] == "no-store" and image.headers["x-content-type-options"] == "nosniff"
            assert image.headers["content-disposition"] == 'inline; filename="preview.jpg"'
        else: assert worker.calls == []


@pytest.mark.parametrize("operation", ["listing", "image"])
@pytest.mark.parametrize("change,code", [("assignment", 404), ("disable", 401), ("password", 403), ("folder", 409)])
def test_source_result_is_reauthorized_after_actual_account_or_material_change(preview_case, operation, change, code):
    case, worker, path = preview_case
    def during_io():
        with case.database.session() as session:
            if change == "assignment": session.get(PBRMaterial, case.materials[0].id).assigned_processor_id = case.users["OTHER"].id
            elif change == "folder": session.get(PBRMaterial, case.materials[0].id).folder_path = "other/" + case.materials[0].technical_identity
            elif change == "password": session.get(UserCredential, case.users["PROCESSOR"].id).must_change_password = True
            else: session.get(InternalUser, case.users["PROCESSOR"].id).is_active = False
            session.commit()
    worker.callback = during_io
    with case.client("PROCESSOR") as client:
        result = client.get(path + ("/previews" if operation == "listing" else "/preview"),
            params={} if operation == "listing" else {"name": "Synthetic preview.png", "expected_sha256": "a" * 64})
        assert result.status_code == code and PIXELS not in result.content


def test_unlinked_folder_or_invalid_selection_never_calls_worker(preview_case):
    case, worker, path = preview_case
    with case.client("ADMIN") as client:
        marker = "PRIVATE_SYNTHETIC_MARKER"
        for name in ("../" + marker + ".png", "C:\\" + marker + ".png", marker + ".svg", "png"):
            result = client.get(path + "/preview", params={"name": name, "expected_sha256": "a" * 64})
            assert result.status_code == 422 and marker not in result.text
        result = client.get(path + "/preview", params={"name": "valid.png", "expected_sha256": marker})
        assert result.status_code == 422 and marker not in result.text
        with case.database.session() as session:
            session.get(PBRMaterial, case.materials[0].id).folder_path = None; session.commit()
        assert client.get(path + "/previews").json()["detail"]["code"] == "PREVIEW_FOLDER_REQUIRED"
        assert worker.calls == []


def test_worker_failures_keep_controlled_statuses_and_no_original_payload(preview_case):
    case, worker, path = preview_case
    with case.client("ADMIN") as client:
        for failure, status in (("PREVIEW_SOURCE_CHANGED", 409), ("PREVIEW_BUSY", 503), ("PREVIEW_UNREADABLE", 422),
                                ("PREVIEW_NOT_FOUND", 404), ("PRIVATE_SYNTHETIC_MARKER", 503)):
            worker.failure = failure
            response = client.get(path + "/previews")
            assert response.status_code == status and "PRIVATE_SYNTHETIC_MARKER" not in response.text


def test_processor_cannot_read_a_folder_with_a_different_identity(preview_case):
    case, worker, path = preview_case
    with case.database.session() as session:
        session.get(PBRMaterial, case.materials[0].id).folder_path = "library/OTHER_0001_G03"; session.commit()
    with case.client("PROCESSOR") as client:
        assert client.get(path + "/previews").status_code == 404
    assert worker.calls == []


def test_invalid_client_hash_is_rejected_before_transport(monkeypatch):
    def forbidden(*_, **__): raise AssertionError("Invalid hash reached transport")
    monkeypatch.setattr(httpx, "stream", forbidden)
    with pytest.raises(PreviewClientError): WorkerPreviewClient("http://worker").image("SAFE_0001_G03", "view.png", "bad")


def test_wire_contract_disables_redirects_and_environment_and_binds_request(monkeypatch):
    calls = []
    def stream(method, url, **kwargs):
        calls.append((method, url, kwargs["json"]))
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return StreamingResponse([json.dumps(listing_payload() if url.endswith("previews") else image_payload()).encode()])
    monkeypatch.setattr(httpx, "stream", stream)
    client = WorkerPreviewClient("http://worker")
    assert len(client.listing("library/SAFE_0001_G03").items) == 1
    assert client.image("library/SAFE_0001_G03", "Synthetic preview.png", "a" * 64).image_bytes() == PIXELS
    assert calls[0][:2] == ("POST", "http://worker/internal/material-previews")
    assert calls[1][2] == {"folder_path": "library/SAFE_0001_G03", "name": "Synthetic preview.png", "expected_sha256": "a" * 64}


@pytest.mark.parametrize("defect", ["folder", "path", "duplicate", "size", "count", "unknown", "missing"])
def test_invalid_worker_list_is_rejected_without_reflection(monkeypatch, defect):
    value = listing_payload()
    if defect == "folder": value["folder_name"] = "OTHER"
    elif defect == "path": value["items"][0]["name"] = "../PRIVATE_SYNTHETIC_MARKER.png"
    elif defect == "duplicate": value["items"].append(value["items"][0])
    elif defect == "size": value["items"][0]["size"] = 64 * 1024**2 + 1
    elif defect == "count": value["ignored_entries"] = 513
    elif defect == "unknown": value["raw_content"] = "PRIVATE_SYNTHETIC_MARKER"
    else: value["missing"] = True
    monkeypatch.setattr(httpx, "stream", lambda *_, **__: StreamingResponse([json.dumps(value).encode()]))
    with pytest.raises(PreviewClientError) as error: WorkerPreviewClient("http://worker").listing("SAFE_0001_G03")
    assert error.value.__cause__ is None and "PRIVATE_SYNTHETIC_MARKER" not in str(error.value)


@pytest.mark.parametrize("defect", ["folder", "name", "source-hash", "output-hash", "size", "base64", "html", "media", "unknown"])
def test_invalid_worker_image_fails_closed(monkeypatch, defect):
    value = image_payload()
    if defect == "folder": value["folder_name"] = "OTHER"
    elif defect == "name": value["name"] = "Other.png"
    elif defect == "source-hash": value["source_sha256"] = "0" * 64
    elif defect == "output-hash": value["sha256"] = "0" * 64
    elif defect == "size": value["width"] = 1025
    elif defect == "base64": value["data"] = "PRIVATE_SYNTHETIC_MARKER"
    elif defect == "html":
        raw = b"<html>PRIVATE_SYNTHETIC_MARKER</html>"; value["data"] = base64.b64encode(raw).decode(); value["sha256"] = hashlib.sha256(raw).hexdigest()
    elif defect == "media": value["media_type"] = "text/html"
    else: value["raw_content"] = "PRIVATE_SYNTHETIC_MARKER"
    monkeypatch.setattr(httpx, "stream", lambda *_, **__: StreamingResponse([json.dumps(value).encode()]))
    with pytest.raises(PreviewClientError) as error: WorkerPreviewClient("http://worker").image("SAFE_0001_G03", "Synthetic preview.png", "a" * 64)
    assert error.value.__cause__ is None and "PRIVATE_SYNTHETIC_MARKER" not in str(error.value)


@pytest.mark.parametrize("declared", [True, False])
def test_stream_limits_apply_even_when_content_length_lies(monkeypatch, declared):
    monkeypatch.setattr("app.preview_client.MAX_RESPONSE_BYTES", 10)
    response = StreamingResponse([b"x" * 11], content_length=11 if declared else 1)
    monkeypatch.setattr(httpx, "stream", lambda *_, **__: response)
    with pytest.raises(PreviewClientError): WorkerPreviewClient("http://worker").listing("SAFE_0001_G03")
    assert response.closed
