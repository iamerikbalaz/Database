import json
import hashlib
from uuid import uuid4

import httpx
from pydantic import SecretStr
import pytest

from app.metadata_client import MetadataClientError, WorkerMetadataClient
from app.metadata_document import rewrite_metadata_json
from test_material_operations import StreamingResponse
from test_source_metadata_edit import VALUES, source


REQUEST = {"folder_path": "library/TEST_0001_G01", "operation_id": str(uuid4()), "expected_sha256": None, "values": VALUES}


def test_private_transport_token_and_request_binding(monkeypatch):
    def transport(method, url, **options):
        assert method == "POST" and url.endswith("/internal/material-metadata-edit")
        assert options["headers"] == {"Authorization": "Bearer " + "s" * 40}
        assert options["trust_env"] is False and options["follow_redirects"] is False
        assert options["json"] == REQUEST
        return StreamingResponse([json.dumps({"operation_id": REQUEST["operation_id"], "status": "COMPLETED",
            "failure_code": None, "metadata": source(REQUEST["folder_path"])}).encode()])
    monkeypatch.setattr(httpx, "stream", transport)
    assert WorkerMetadataClient("http://worker", SecretStr("s" * 40), True).execute(REQUEST).status == "COMPLETED"


@pytest.mark.parametrize("values", [
    {"hex_color": "#ABCDEF", "width_cm": None, "height_cm": None},
    {"hex_color": None, "width_cm": None, "height_cm": None},
    {"hex_color": "#ABCDEF", "width_cm": "12.5", "height_cm": None},
])
def test_complete_template_explicit_null_dimensions_are_valid_worker_completion(monkeypatch, values):
    identity = {"FOLDER": "TEST_0001_G01", "MANUFACTURER": "Test", "PRODUCT_NUMBER": "0001",
        "PRODUCT_NAME": "Test", "CATEGORY": "G01", "BASE_NAME": "TEST_0001"}
    request = {**REQUEST, "values": values, "identity": identity, "source_filename": "metadata.json"}
    raw = rewrite_metadata_json(None, values, identity)
    body = {"operation_id": request["operation_id"], "status": "COMPLETED", "failure_code": None,
        "metadata": {"schema_version": 1, "source_filename": "metadata.json", "folder_name": identity["FOLDER"],
            "status": "WARNING", "sha256": hashlib.sha256(raw).hexdigest(), "raw_content": raw.decode(), **values}}
    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: StreamingResponse([json.dumps(body).encode()]))
    result = WorkerMetadataClient("http://worker", SecretStr("s" * 40), True).execute(request)
    assert result.status == "COMPLETED" and result.metadata.height_cm is None
    assert json.loads(result.metadata.raw_content)["TEXTURE_SIZE"]["cm"]["height"] is None


@pytest.mark.parametrize("unsupported", [True, False, "12.5", [], {}, "NaN"])
def test_null_dimension_support_does_not_accept_unsupported_json_types(monkeypatch, unsupported):
    values = {"hex_color": "#ABCDEF", "width_cm": None, "height_cm": None}
    request = {**REQUEST, "values": values, "source_filename": "metadata.json"}
    document = json.loads(rewrite_metadata_json(None, values))
    document["TEXTURE_SIZE"]["cm"]["width"] = unsupported
    raw = json.dumps(document).encode()
    body = {"operation_id": request["operation_id"], "status": "COMPLETED", "failure_code": None,
        "metadata": {"schema_version": 1, "source_filename": "metadata.json", "folder_name": "TEST_0001_G01",
            "status": "WARNING", "sha256": hashlib.sha256(raw).hexdigest(), "raw_content": raw.decode(), **values}}
    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: StreamingResponse([json.dumps(body).encode()]))
    with pytest.raises(MetadataClientError):
        WorkerMetadataClient("http://worker", SecretStr("s" * 40), True).execute(request)


@pytest.mark.parametrize("change", ["operation", "folder", "hash", "color", "bytes", "extra", "version", "unicode_size"])
def test_untrusted_response_is_rejected_without_reflection(monkeypatch, change):
    body = {"operation_id": REQUEST["operation_id"], "status": "COMPLETED", "failure_code": None, "metadata": source(REQUEST["folder_path"])}
    if change == "operation": body["operation_id"] = str(uuid4())
    if change == "folder": body["metadata"]["folder_name"] = "PRIVATE_0001_G01"
    if change == "hash": body["metadata"]["sha256"] = "a" * 64
    if change == "color": body["metadata"]["hex_color"] = "#FFFFFF"
    if change == "extra": body["PRIVATE"] = "SYNTHETIC_PRIVATE"
    if change == "version": body["metadata"]["schema_version"] = True
    if change == "unicode_size":
        import hashlib
        body["metadata"]["raw_content"] = "é" * (2 * 1024 * 1024 + 1)
        body["metadata"]["sha256"] = hashlib.sha256(body["metadata"]["raw_content"].encode()).hexdigest()
    if change == "bytes":
        import hashlib
        body["metadata"]["raw_content"] = '{}'
        body["metadata"]["sha256"] = hashlib.sha256(b'{}').hexdigest()
    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: StreamingResponse([json.dumps(body).encode()]))
    with pytest.raises(MetadataClientError) as error:
        WorkerMetadataClient("http://worker", SecretStr("s" * 40), True).execute(REQUEST)
    assert "PRIVATE" not in str(error.value) and error.value.__cause__ is None


def test_disabled_mutation_never_contacts_worker(monkeypatch):
    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: pytest.fail("unexpected network"))
    with pytest.raises(MetadataClientError): WorkerMetadataClient("http://worker").execute(REQUEST)


def test_response_declared_size_is_bounded_before_reading(monkeypatch):
    response = StreamingResponse([b"private"], content_length=27 * 1024 * 1024)
    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: response)
    with pytest.raises(MetadataClientError): WorkerMetadataClient("http://worker").inspect(REQUEST["folder_path"])
    assert response.closed and not response.iterated
