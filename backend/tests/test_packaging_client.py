"""Contract fixtures come from actual synthetic HTTP/conversion/restart smoke."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
from pydantic import SecretStr
import pytest

from app import packaging_client as transport
from app.material_review import canonical_hash
from app.packaging_client import PackagingClientError, WorkerPackagingClient
from app.packaging_contract import PackagingResult, PreparedPackaging
from test_material_operations import StreamingResponse

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name="packaging-contract.json"):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def prepared(value): return PreparedPackaging.model_validate_json(json.dumps(value["prepared"]))


def initial(value):
    request = value["prepared"]["request"]
    return {"operation_id": request["operation_id"], "parts": request["parts"],
        "expected_source_revision_hash": request["source_revision_hash"], "expected_technical_report_hash": request["technical_report_hash"],
        "approval_context_hash": request["approval_context_hash"], "policy": request["policy"], "storage_timezone": request["storage_timezone"],
        "limits": request["limits"], "report": value["report"]}


def client(**changes):
    return WorkerPackagingClient("http://packaging:8081", token=changes.pop("token", SecretStr(uuid4().hex + uuid4().hex)),
        enabled=changes.pop("enabled", True), **changes)


def response(value, *, status=200, declared=None):
    result = StreamingResponse([json.dumps(value).encode()], status_code=status, content_length=declared)
    result.headers["Content-Type"] = "application/json"
    return result


def rehash(value):
    stored = value["stored"]
    stored["payload"]["bundle_sha256"] = canonical_hash(stored["payload"]["bundle"])
    stored["proof_sha256"] = canonical_hash(stored["payload"])


@pytest.mark.parametrize("name", ["packaging-contract.json", "packaging-contract-multi-current.json",
    "packaging-contract-nonstandard.json", "packaging-contract-square.json"])
def test_actual_service_fixtures_bind_request_report_layout_and_all_proofs(name):
    value = fixture(name); bound = prepared(value)
    bound.verify_preparation(initial(value))
    result = PackagingResult.model_validate_json(json.dumps(value["result"]))
    result.verify_request(bound, value["report"])
    assert result.status == "READY" and result.stored is not None
    if name.endswith("nonstandard.json"):
        assert [item.filename.rsplit("_", 1)[-1] for item in result.stored.payload.bundle.archives] == ["3K.zip", "2K.zip", "1K.zip"]
    if name.endswith("square.json"):
        assert all(item.conversion is None for item in result.stored.payload.bundle.maps)


def test_private_transport_prepares_executes_and_reconciles_without_proxies_or_redirects(monkeypatch):
    value = fixture(); service = client(); calls = []
    def stream(method, url, **kwargs):
        action = url.rsplit("/", 1)[-1]
        assert method == "POST" and url == "http://packaging:8081/internal/packaging/" + action
        assert kwargs["headers"]["Authorization"] == "Bearer " + service._token.get_secret_value()
        assert kwargs["headers"]["Content-Type"] == "application/json"
        assert not kwargs["trust_env"] and not kwargs["follow_redirects"] and kwargs["timeout"].connect == 5
        body = json.loads(kwargs["content"])
        if action == "prepare": assert body == initial(value)
        elif action == "execute": assert body == {**value["prepared"], "report": value["report"], "retry": False}
        else: assert body == value["prepared"]
        calls.append(action)
        return response(value["prepared"] if action == "prepare" else value["result"])
    monkeypatch.setattr(httpx, "stream", stream)
    bound = service.prepare(initial(value))
    assert service.execute(bound, value["report"]).status == "READY"
    assert service.reconcile(bound, value["report"]).status == "READY"
    assert calls == ["prepare", "execute", "reconcile"]


@pytest.mark.parametrize("change", ["operation", "context", "source", "report", "policy", "timezone", "parts", "limits", "extra", "hash"])
def test_prepared_response_cannot_substitute_approved_inputs(monkeypatch, change):
    value = fixture(); result = copy.deepcopy(value["prepared"]); request = result["request"]
    if change == "operation": request["operation_id"] = str(uuid4())
    elif change == "context": request["approval_context_hash"] = "b" * 64
    elif change == "source": request["source_revision_hash"] = "b" * 64
    elif change == "report": request["technical_report_hash"] = "b" * 64
    elif change == "policy": request["policy"] = "CURRENT_ON_OR_AFTER_2026_03_04"
    elif change == "timezone": request["storage_timezone"] = "Europe/Prague"
    elif change == "parts": request["parts"].insert(0, "another")
    elif change == "limits": request["limits"]["seconds"] = 1700
    elif change == "extra": request["raw_metadata"] = "SYNTHETIC_PRIVATE"
    result["request_hash"] = "b" * 64 if change == "hash" else canonical_hash(request)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: response(result))
    with pytest.raises(PackagingClientError) as caught: client().prepare(initial(value))
    assert caught.value.code == "PACKAGING_UNAVAILABLE" and "PRIVATE" not in str(caught.value) and caught.value.__cause__ is None


@pytest.mark.parametrize("change", ["operation", "request", "status", "attempt", "missing", "plan", "bundle-operation", "timezone", "policy",
    "map-source", "map-input", "geometry", "bits", "conversion", "runtime-policy", "duplicate-resolution", "missing-map", "manifest",
    "preview", "extra-file", "duplicate-file", "legacy-date", "path", "crc", "filename", "order", "extra-proof", "retention-history", "outer-hash"])
def test_rehashed_semantically_corrupt_results_are_rejected_without_reflection(monkeypatch, change):
    value = fixture(); result = copy.deepcopy(value["result"])
    stored = result["stored"]; payload = stored["payload"]; bundle = payload["bundle"]
    map_proof = bundle["maps"][0]; archive = bundle["archives"][0]
    if change == "operation": result["operation_id"] = str(uuid4())
    elif change == "request": result["request_hash"] = "b" * 64
    elif change == "status": result["status"] = "RETRY_REQUIRED"
    elif change == "attempt": result["attempt"] = 33
    elif change == "missing": result["stored"] = None
    elif change == "plan": stored["plan_hash"] = bundle["plan_sha256"] = "b" * 64
    elif change == "bundle-operation": bundle["operation_id"] = str(uuid4())
    elif change == "timezone": bundle["storage_timezone"] = archive["storage_timezone"] = "Europe/Prague"
    elif change == "policy": archive["policy"] = "CURRENT_ON_OR_AFTER_2026_03_04"
    elif change == "map-source": map_proof["operation"]["source"] = "SOURCE/private"
    elif change == "map-input": map_proof["input_sha256"] = map_proof["conversion"]["input_sha256"] = "b" * 64
    elif change == "geometry": map_proof["operation"]["width"] = map_proof["conversion"]["width"] = 512
    elif change == "bits": map_proof["bits"] = map_proof["operation"]["bits"] = map_proof["conversion"]["bits"] = 16
    elif change == "conversion": map_proof["conversion"]["sha256"] = "b" * 64
    elif change == "runtime-policy": map_proof["conversion"]["policy_sha256"] = "b" * 64
    elif change == "duplicate-resolution": bundle["archives"].append(copy.deepcopy(archive))
    elif change == "missing-map": bundle["maps"] = []
    elif change == "manifest":
        bundle["manifest_sha256"] = "b" * 64
        for item in payload["files"]:
            if item["path"] == "metadata.json": item["sha256"] = "b" * 64
        for item in archive["entries"]:
            if item["path"].endswith("/metadata.json"): item["sha256"] = "b" * 64
    elif change == "preview":
        payload["files"] = [item for item in payload["files"] if not item["path"].startswith("PREVIEW/")]
        archive["entries"] = [item for item in archive["entries"] if not item["path"].endswith("/preview.png")]
    elif change == "extra-file": payload["files"].append({"path": "private.txt", "size": 1, "sha256": "b" * 64})
    elif change == "duplicate-file": payload["files"].append(copy.deepcopy(payload["files"][0]))
    elif change == "legacy-date": archive["entries"][0]["date_time"][0] = 2025
    elif change == "path": archive["entries"][0]["path"] = "../SYNTHETIC_PRIVATE/"
    elif change == "crc": archive["entries"][0]["crc32"] = 2**32
    elif change == "filename": archive["filename"] = "OTHER_0001_G03_1K.zip"
    elif change == "order": payload["files"].reverse()
    elif change == "extra-proof": map_proof["conversion"]["raw_metadata"] = "SYNTHETIC_PRIVATE"
    elif change == "retention-history": stored["attempt_history"] = [{"attempt": 1, "proof_sha256": "b" * 64, "outcome": "INCOMPLETE"}]
    if result["stored"] is not None: rehash(result)
    if change == "outer-hash": stored["proof_sha256"] = "b" * 64
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: response(result))
    with pytest.raises(PackagingClientError) as caught: client().execute(prepared(value), value["report"])
    assert "PRIVATE" not in str(caught.value) and caught.value.__cause__ is None


def test_incomplete_result_is_bound_but_does_not_claim_artifact_success(monkeypatch):
    value = fixture(); result = {**value["result"], "status": "RETRY_REQUIRED", "stored": None}
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: response(result))
    received = client().reconcile(prepared(value), value["report"])
    assert received.status == "RETRY_REQUIRED" and received.stored is None


@pytest.mark.parametrize("limit", ["staged_bytes", "generated_bytes", "retained_bytes"])
def test_ready_result_must_fit_every_frozen_byte_budget(monkeypatch, limit):
    value = fixture(); value["prepared"]["request"]["limits"][limit] = 1
    value["prepared"]["request_hash"] = canonical_hash(value["prepared"]["request"])
    value["result"]["request_hash"] = value["prepared"]["request_hash"]
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: response(value["result"]))
    with pytest.raises(PackagingClientError): client().execute(prepared(value), value["report"])


@pytest.mark.parametrize("config", [{"enabled": False}, {"token": None}, {"token": "not-a-secret-wrapper"},
    {"token": SecretStr("short")}, {"token": SecretStr("x" * 257)}, {"token": SecretStr("x" * 31 + "\n")}])
def test_disabled_or_invalid_credentials_never_reach_network(monkeypatch, config):
    def forbidden(*a, **k): pytest.fail("Disabled service reached network")
    monkeypatch.setattr(httpx, "stream", forbidden)
    with pytest.raises(PackagingClientError) as caught: client(**config).prepare({})
    assert caught.value.code == "PACKAGING_SERVICE_DISABLED"


@pytest.mark.parametrize("url", ["file:///tmp/private", "http://name:secret@service", "http://service/path", "http://service?private",
    "http://service#private", "http://service\\private", "http://service:70000"])
def test_service_url_cannot_forward_credentials_or_use_unexpected_targets(url):
    with pytest.raises(ValueError): WorkerPackagingClient(url)


@pytest.mark.parametrize("failure", ["timeout", "malformed", "html", "compressed", "declared-large", "actual-large", "length-mismatch", "deadline", "redirect", "unknown-code"])
def test_transport_failure_is_bounded_private_closed_and_never_retried(monkeypatch, failure):
    value = fixture(); calls = []
    result = response(value["result"])
    if failure == "malformed": result = StreamingResponse([b"SYNTHETIC_PRIVATE"]); result.headers["Content-Type"] = "application/json"
    elif failure == "html": result.headers["Content-Type"] = "text/html"
    elif failure == "compressed": result.headers["Content-Encoding"] = "gzip"
    elif failure == "declared-large": result.headers["Content-Length"] = str(transport.MAX_RESPONSE_BYTES + 1)
    elif failure == "actual-large": monkeypatch.setattr(transport, "MAX_RESPONSE_BYTES", 8)
    elif failure == "length-mismatch": result.headers["Content-Length"] = "1"
    elif failure == "deadline":
        ticks = iter([0, 4000]); monkeypatch.setattr(transport, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    elif failure == "redirect": result.status_code = 307
    elif failure == "unknown-code": result = response({"detail": {"code": "SYNTHETIC_PRIVATE"}}, status=503)
    def stream(*args, **kwargs):
        calls.append(1)
        if failure == "timeout": raise httpx.ReadTimeout("SYNTHETIC_PRIVATE")
        return result
    monkeypatch.setattr(httpx, "stream", stream)
    with pytest.raises(PackagingClientError) as caught: client().execute(prepared(value), value["report"])
    assert len(calls) == 1 and caught.value.code == "PACKAGING_UNAVAILABLE"
    assert "PRIVATE" not in str(caught.value) and caught.value.__cause__ is None
    if failure != "timeout": assert result.closed


def test_known_fixed_failure_is_preserved_without_response_diagnostics(monkeypatch):
    value = fixture()
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: response({"detail": {
        "code": "PACKAGING_EXECUTION_RECOVERY_REQUIRED", "private": "SYNTHETIC_PRIVATE"}}, status=409))
    with pytest.raises(PackagingClientError) as caught: client().execute(prepared(value), value["report"])
    assert caught.value.code == "PACKAGING_EXECUTION_RECOVERY_REQUIRED" and "PRIVATE" not in str(caught.value)


def test_oversized_request_or_mutated_prepared_model_never_reaches_network(monkeypatch):
    value = fixture()
    def forbidden(*a, **k): pytest.fail("Invalid request reached network")
    monkeypatch.setattr(httpx, "stream", forbidden)
    with monkeypatch.context() as patch:
        patch.setattr(transport, "MAX_REQUEST_BYTES", 8)
        with pytest.raises(PackagingClientError) as caught: client().prepare(initial(value))
        assert caught.value.code == "PACKAGING_REQUEST_TOO_LARGE"
    bound = prepared(value); bound.request.parts.append("SYNTHETIC_PRIVATE")
    with pytest.raises(PackagingClientError): client().execute(bound, value["report"])
