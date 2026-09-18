"""Ordered envelope validation reuses independently verified real artifact proofs."""
import copy
import json
from uuid import uuid4

import httpx
import pytest

from app.packaging_client import PackagingClientError
from app.packaging_contract import PackagingDispatch, DispatchedPackagingResult
from test_packaging_client import client, fixture, prepared, response


def command(action="EXECUTE", ordinal=1):
    return PackagingDispatch(id=str(uuid4()), ordinal=ordinal, action=action)


def result(value, action, terminal=None):
    return {**copy.deepcopy(value["result"]), "dispatch": action.model_dump(mode="json"),
        "terminal": terminal or ("CLOSED" if action.action == "CLOSE" else "OPEN")}


@pytest.mark.parametrize("kind,number,terminal", [("EXECUTE", 1, "OPEN"), ("RETRY", 3, "OPEN"),
    ("RECONCILE", 4, "OPEN"), ("RECONCILE", 5, "CLOSING"), ("RECONCILE", 6, "CLOSED"), ("CLOSE", 7, "CLOSED")])
def test_ordered_transport_binds_command_and_omits_report_on_recovery(monkeypatch, kind, number, terminal):
    value = fixture(); action = command(kind, number); calls = []
    def stream(method, url, **kwargs):
        assert method == "POST" and url.endswith("/internal/packaging/dispatch")
        body = json.loads(kwargs["content"])
        assert body == {**value["prepared"], "dispatch": action.model_dump(mode="json"),
            **({"report": value["report"]} if kind in {"EXECUTE", "RETRY"} else {})}
        assert not kwargs["trust_env"] and not kwargs["follow_redirects"]
        calls.append(url); return response(result(value, action, terminal))
    monkeypatch.setattr(httpx, "stream", stream)
    observed = client().dispatch(prepared(value), value["report"], action)
    assert observed.dispatch == action and observed.terminal == terminal and len(calls) == 1
    assert observed.stored.proof_sha256 == value["result"]["stored"]["proof_sha256"]


@pytest.mark.parametrize("change", ["id", "ordinal", "action", "missing-control", "terminal", "proof", "request", "extra"])
def test_response_cannot_substitute_dispatch_or_omit_closed_fence(monkeypatch, change):
    value = fixture(); action = command("CLOSE", 3); changed = result(value, action)
    if change == "id": changed["dispatch"]["id"] = str(uuid4())
    elif change == "ordinal": changed["dispatch"]["ordinal"] += 1
    elif change == "action": changed["dispatch"]["action"] = "RECONCILE"
    elif change == "missing-control": changed.pop("dispatch")
    elif change == "terminal": changed["terminal"] = "CLOSING"
    elif change == "proof": changed["stored"]["proof_sha256"] = "a" * 64
    elif change == "request": changed["request_hash"] = "b" * 64
    else: changed["private"] = "SYNTHETIC_PRIVATE"
    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: response(changed))
    with pytest.raises(PackagingClientError) as failure: client().dispatch(prepared(value), value["report"], action)
    assert failure.value.code == "PACKAGING_UNAVAILABLE" and "PRIVATE" not in str(failure.value)


@pytest.mark.parametrize("code", ["PACKAGING_DISPATCH_STALE", "PACKAGING_DISPATCH_CONFLICT", "PACKAGING_EXECUTION_CLOSED"])
def test_fixed_ordering_failures_never_trigger_an_implicit_retry(monkeypatch, code):
    value = fixture(); calls = []
    def stream(*args, **kwargs):
        calls.append(1); return response({"detail": {"code": code}}, status=409)
    monkeypatch.setattr(httpx, "stream", stream)
    with pytest.raises(PackagingClientError) as failure: client().dispatch(prepared(value), value["report"], command())
    assert failure.value.code == code and calls == [1]


def test_lost_network_response_is_uncertain_and_not_retried(monkeypatch):
    value = fixture(); calls = []
    def stream(*args, **kwargs): calls.append(1); raise httpx.ReadTimeout("Synthetic response loss")
    monkeypatch.setattr(httpx, "stream", stream)
    with pytest.raises(PackagingClientError): client().dispatch(prepared(value), value["report"], command())
    assert calls == [1]


def test_model_construct_cannot_bypass_command_validation_before_io(monkeypatch):
    value = fixture()
    invalid = PackagingDispatch.model_construct(id=str(uuid4()), ordinal=2, action="EXECUTE")
    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: pytest.fail("Invalid command reached the service"))
    with pytest.raises(PackagingClientError): client().dispatch(prepared(value), value["report"], invalid)


def test_known_incomplete_closed_job_carries_no_artifact_proof():
    value = fixture(); action = command("CLOSE", 3)
    body = {**result(value, action), "status": "RETRY_REQUIRED", "stored": None}
    checked = DispatchedPackagingResult.model_validate_json(json.dumps(body))
    checked.verify_request(prepared(value), value["report"]); checked.verify_dispatch(action)
    assert checked.terminal == "CLOSED" and checked.stored is None


def test_actual_production_image_ordered_restart_fixture_validates_every_envelope():
    value = fixture("packaging-dispatch-contract.json")
    bound = prepared(value); proofs = []
    for field, ordinal, kind, terminal in (("result", 1, "EXECUTE", "OPEN"),
            ("recovered", 2, "RECONCILE", "OPEN"), ("closed", 3, "CLOSE", "CLOSED")):
        checked = DispatchedPackagingResult.model_validate_json(json.dumps(value[field]))
        checked.verify_request(bound, value["report"])
        checked.verify_dispatch(PackagingDispatch.model_validate(value[field]["dispatch"]))
        assert (checked.dispatch.ordinal, checked.dispatch.action, checked.terminal) == (ordinal, kind, terminal)
        proofs.append(checked.stored.proof_sha256)
    assert len(set(proofs)) == 1
