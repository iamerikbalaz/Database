"""A receipt must independently bind every accepted historical packaging fact."""
import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app import packaging_client as transport
from app.material_review import canonical_hash
from app.packaging_client import PackagingClientError
from app.packaging_contract import DispatchedPackagingResult
from app.packaging_retirement_contract import PackagingRetirementCommand, PackagingRetirementReceipt
from test_packaging_client import client, fixture, prepared, response
from test_material_operations import StreamingResponse


def accepted(value): return DispatchedPackagingResult.model_validate_json(json.dumps(value["closed"]))


def command(value):
    return PackagingRetirementCommand(retirement_id=str(uuid4()), proof_sha256=value["closed"]["stored"]["proof_sha256"])


def receipt(value, action):
    bound = prepared(value); stored = value["closed"]["stored"]
    return {**action.document(bound), "status": "REMOVED", "retirement_request_hash": canonical_hash(action.document(bound)),
        "file_count": len(stored["payload"]["files"]), "byte_count": sum(item["size"] for item in stored["payload"]["files"])}


@pytest.mark.parametrize("configured_timeout,expected_timeout", [(3630, 150), (15, 15)])
def test_exact_private_retirement_transmits_only_frozen_bindings_and_verifies_receipt(monkeypatch, configured_timeout, expected_timeout):
    value = fixture("packaging-dispatch-contract.json"); action = command(value); calls = []
    expected = receipt(value, action)
    def stream(method, url, **kwargs):
        assert method == "POST" and url.endswith("/internal/packaging/retire")
        assert json.loads(kwargs["content"]) == {**value["prepared"], **action.model_dump(mode="json")}
        assert kwargs["timeout"].read == expected_timeout and kwargs["timeout"].connect == 5
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        calls.append(1); return response(expected)
    monkeypatch.setattr(httpx, "stream", stream)
    result = client(timeout_seconds=configured_timeout).retire(prepared(value), value["report"], accepted(value), action)
    assert result.model_dump(mode="json") == expected and calls == [1]


@pytest.mark.parametrize("field", ["schema_version", "status", "operation_id", "request_hash", "plan_hash", "proof_sha256",
    "retirement_id", "retirement_request_hash", "file_count", "byte_count", "extra", "missing", "boolean-version", "boolean-count", "float-size"])
def test_a_plausible_response_cannot_replace_any_accepted_fact(monkeypatch, field):
    value = fixture("packaging-dispatch-contract.json"); action = command(value); changed = receipt(value, action)
    if field in {"operation_id", "retirement_id"}: changed[field] = str(uuid4())
    elif field in {"schema_version", "file_count", "byte_count"}: changed[field] += 1
    elif field == "status": changed[field] = "READY"
    elif field == "extra": changed["path"] = "SYNTHETIC_PRIVATE"
    elif field == "missing": changed.pop("retirement_request_hash")
    elif field == "boolean-version": changed["schema_version"] = True
    elif field == "boolean-count": changed["file_count"] = True
    elif field == "float-size": changed["byte_count"] = float(changed["byte_count"])
    else: changed[field] = "b" * 64
    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: response(changed))
    with pytest.raises(PackagingClientError) as caught:
        client().retire(prepared(value), value["report"], accepted(value), action)
    assert caught.value.code == "PACKAGING_UNAVAILABLE" and "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("change", ["prepared", "report", "result", "incomplete", "closing", "proof", "invalid-command", "disabled"])
def test_unverified_input_never_sends_a_destructive_command(monkeypatch, change):
    value = fixture("packaging-dispatch-contract.json"); action = command(value)
    bound, saved, report = prepared(value), accepted(value), copy.deepcopy(value["report"])
    if change == "prepared": bound.request.parts.append("SYNTHETIC_PRIVATE")
    elif change == "report": report["can_approve"] = False
    elif change == "result": saved.stored.payload.files.pop()
    elif change == "incomplete": saved = saved.model_copy(update={"status": "RETRY_REQUIRED", "stored": None})
    elif change == "closing":
        saved = saved.model_copy(update={"terminal": "CLOSING", "dispatch": saved.dispatch.model_copy(update={"action": "RECONCILE"})})
    elif change == "proof": action = action.model_copy(update={"proof_sha256": "b" * 64})
    elif change == "invalid-command": action = PackagingRetirementCommand.model_construct(retirement_id="invalid", proof_sha256=action.proof_sha256)
    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: pytest.fail("Invalid retirement reached the worker"))
    with pytest.raises(PackagingClientError): client(enabled=change != "disabled").retire(bound, report, saved, action)


@pytest.mark.parametrize("failure", ["timeout", "declared-large", "actual-large", "deadline", "truncated", "compressed", "html", "redirect", "unknown-code"])
def test_retirement_transport_is_small_bounded_private_and_never_retries(monkeypatch, failure):
    value = fixture("packaging-dispatch-contract.json"); action = command(value); calls = []
    result = response(receipt(value, action))
    if failure == "declared-large": result.headers["Content-Length"] = "4097"
    elif failure == "actual-large":
        result = StreamingResponse([b" " * 4097]); result.headers["Content-Type"] = "application/json"
    elif failure == "deadline":
        ticks = iter([0, 151]); monkeypatch.setattr(transport, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    elif failure == "truncated": result.headers["Content-Length"] = "4000"
    elif failure == "compressed": result.headers["Content-Encoding"] = "gzip"
    elif failure == "html": result.headers["Content-Type"] = "text/html"
    elif failure == "redirect": result.status_code = 307
    elif failure == "unknown-code": result = response({"detail": {"code": "SYNTHETIC_PRIVATE"}}, status=409)
    def stream(*args, **kwargs):
        calls.append(1)
        if failure == "timeout": raise httpx.ReadTimeout("SYNTHETIC_PRIVATE")
        return result
    monkeypatch.setattr(httpx, "stream", stream)
    with pytest.raises(PackagingClientError) as caught:
        client().retire(prepared(value), value["report"], accepted(value), action)
    assert calls == [1] and caught.value.code == "PACKAGING_UNAVAILABLE"
    assert "PRIVATE" not in str(caught.value) and caught.value.__cause__ is None
    if failure != "timeout": assert result.closed


@pytest.mark.parametrize("code", ["PACKAGING_RETIREMENT_DISABLED", "PACKAGING_RETIREMENT_REQUEST_CONFLICT",
    "PACKAGING_RETIREMENT_NOT_READY", "PACKAGING_STORE_RETIRED", "PACKAGING_STORE_BUSY", "PACKAGING_LEASE_BUSY"])
def test_known_failures_retain_only_the_allowlisted_code(monkeypatch, code):
    value = fixture("packaging-dispatch-contract.json"); calls = []
    def stream(*args, **kwargs):
        calls.append(1); return response({"detail": {"code": code, "private": "SYNTHETIC_PRIVATE"}}, status=409)
    monkeypatch.setattr(httpx, "stream", stream)
    with pytest.raises(PackagingClientError) as caught:
        client().retire(prepared(value), value["report"], accepted(value), command(value))
    assert calls == [1] and caught.value.code == code and "PRIVATE" not in str(caught.value)


def test_real_http_restart_and_lost_reply_fixture_passes_independent_backend_validation(monkeypatch):
    value = fixture("packaging-retirement-contract.json"); bound = prepared(value); saved = accepted(value)
    saved.verify_request(bound, value["report"])
    observed = PackagingRetirementReceipt.model_validate_json(json.dumps(value["retirement"]))
    action = PackagingRetirementCommand(retirement_id=observed.retirement_id, proof_sha256=saved.stored.proof_sha256)
    observed.verify(bound, saved, action)
    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: response(value["retirement"]))
    assert client().retire(bound, value["report"], saved, action) == observed
