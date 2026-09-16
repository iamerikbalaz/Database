import json
from uuid import uuid4

import httpx
from pydantic import SecretStr
import pytest

from app.identity_client import IdentityClientError, WorkerIdentityClient
from test_material_identity import plan_payload
from test_material_operations import StreamingResponse

REQUEST = {"folder_path": "old/SAFE_0001_G03", "target_path": "new/NEXT_0007_G04", "brand_name": "Synthetic", "material_name": "Fixture"}


def test_plan_transport_checks_hash_and_never_uses_proxies_or_redirects(monkeypatch):
    def stream(method, url, **kwargs):
        assert method == "POST" and url == "http://worker:8080/internal/material-identity-plan"
        assert kwargs["json"] == REQUEST and kwargs["headers"] == {}
        assert not kwargs["trust_env"] and not kwargs["follow_redirects"]
        return StreamingResponse([json.dumps(plan_payload(REQUEST)).encode()])
    monkeypatch.setattr(httpx, "stream", stream)
    assert WorkerIdentityClient("http://worker:8080").plan(REQUEST).ready


@pytest.mark.parametrize("change", ["hash", "raw", "source", "target", "rename", "finding", "ready", "metadata"])
def test_untrusted_worker_plan_is_rejected_without_reflection(monkeypatch, change):
    result = plan_payload(REQUEST)
    if change == "hash": result["plan_hash"] = "f" * 64
    if change == "raw": result["raw_metadata"] = "SYNTHETIC_PRIVATE"
    if change == "source": result["source_path"] = "../SYNTHETIC_PRIVATE"
    if change == "target": result["target_path"] = "another/TARGET_0001_G03"
    if change == "rename": result["changes"][0]["target"] = "SYNTHETIC_PRIVATE"
    if change == "finding": result["errors"] = [{"code": "SYNTHETIC_PRIVATE", "path": "metadata.txt"}]
    if change == "ready": result["ready"] = False
    if change == "metadata": result["metadata"]["changed_fields"] = ["SYNTHETIC_PRIVATE"]
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: StreamingResponse([json.dumps(result).encode()]))
    with pytest.raises(IdentityClientError) as caught: WorkerIdentityClient("http://worker").plan(REQUEST)
    assert "PRIVATE" not in str(caught.value) and caught.value.__cause__ is None


def test_source_writes_require_explicit_enable_and_private_service_credential(monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail("Disabled source operation reached network")
    monkeypatch.setattr(httpx, "stream", forbidden)
    for client in (WorkerIdentityClient("http://worker"), WorkerIdentityClient("http://worker", enabled=True)):
        with pytest.raises(IdentityClientError, match="could not be verified") as caught: client.execute({})
        assert caught.value.code == "SOURCE_MUTATIONS_DISABLED"


@pytest.mark.parametrize("corrupt", [False, True])
def test_execution_uses_service_token_and_binds_response_to_operation(monkeypatch, corrupt):
    request = {**REQUEST, "operation_id": str(uuid4()), "expected_plan_hash": "a" * 64}
    token = SecretStr(uuid4().hex + uuid4().hex)
    def stream(method, url, **kwargs):
        assert kwargs["headers"]["Authorization"] == "Bearer " + token.get_secret_value()
        assert not kwargs["trust_env"] and not kwargs["follow_redirects"]
        result = {"operation_id": str(uuid4()) if corrupt else request["operation_id"], "status": "COMPLETED",
            "plan_hash": request["expected_plan_hash"], "source_path": REQUEST["folder_path"], "target_path": REQUEST["target_path"],
            "source_revision_hash": "b" * 64, "target_revision_hash": "c" * 64, "failure_code": None}
        return StreamingResponse([json.dumps(result).encode()])
    monkeypatch.setattr(httpx, "stream", stream)
    client = WorkerIdentityClient("http://worker", token, True)
    if corrupt:
        with pytest.raises(IdentityClientError): client.execute(request)
    else: assert str(client.execute(request).operation_id) == request["operation_id"]


def test_response_bound_and_private_worker_diagnostics(monkeypatch):
    monkeypatch.setattr("app.identity_client.MAX_IDENTITY_RESPONSE_BYTES", 8)
    response = StreamingResponse([b"x" * 9], content_length=1)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: response)
    with pytest.raises(IdentityClientError): WorkerIdentityClient("http://worker").plan(REQUEST)
    assert response.closed
    monkeypatch.setattr("app.identity_client.MAX_IDENTITY_RESPONSE_BYTES", 10000)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: StreamingResponse([b'{"detail":{"code":"SYNTHETIC_PRIVATE"}}'], status_code=503))
    with pytest.raises(IdentityClientError) as caught: WorkerIdentityClient("http://worker").plan(REQUEST)
    assert caught.value.code == "IDENTITY_UNAVAILABLE"
