"""Opt-in private retirement requires a recorded execution and preserves its history."""
from dataclasses import replace
import json
import os
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app import packaging_retirement_execution as retirement
from app.packaging_api import PackagingServiceSettings, create_packaging_app
from test_packaging_assembly import snapshot
from test_packaging_api import BASE, headers, packaging_runtime, prepared, settings
from test_packaging_dispatch_api import payload
from test_packaging_execution import job, state

pytestmark = pytest.mark.usefixtures("packaging_runtime")


def removal(job, proof="a" * 64):
    return {**prepared(job), "retirement_id": str(uuid4()), "proof_sha256": proof}


def test_retirement_stays_disabled_unless_explicitly_enabled(job, monkeypatch):
    monkeypatch.delenv("PACKAGING_RETIREMENT_ENABLED", raising=False)
    assert PackagingServiceSettings.from_environment().retirement_enabled is False
    config = settings(job)
    with TestClient(create_packaging_app(config)) as client:
        denied = client.post(BASE + "retire", json=removal(job))
        assert denied.status_code == 401
        disabled = client.post(BASE + "retire", json=removal(job), headers=headers(config))
        assert disabled.status_code == 503 and disabled.json()["detail"]["code"] == "PACKAGING_RETIREMENT_DISABLED"
    assert not list(job[1].artifacts.iterdir()) and not list(job[1].journal.iterdir())


def test_retirement_cannot_create_a_missing_execution(job):
    config = replace(settings(job), retirement_enabled=True)
    with TestClient(create_packaging_app(config)) as client:
        result = client.post(BASE + "retire", json=removal(job), headers=headers(config))
        assert result.status_code == 404 and result.json()["detail"]["code"] == "PACKAGING_EXECUTION_NOT_FOUND"
    assert not list(job[1].artifacts.iterdir()) and not list(job[1].journal.iterdir())


@pytest.mark.parametrize("closed", [False, True])
def test_actual_private_retirement_survives_restart_offline_nas_and_preserves_execution_history(job, closed):
    config = replace(settings(job), retirement_enabled=True); source = snapshot(job[0][2])
    first = payload(job)
    with TestClient(create_packaging_app(config)) as client:
        response = client.post(BASE + "dispatch", json=first, headers=headers(config))
        assert response.status_code == 200
        completed = response.json(); body = removal(job, completed["stored"]["proof_sha256"])
        if closed:
            result = client.post(BASE + "dispatch", json=payload(job, "CLOSE", 2), headers=headers(config))
            assert result.status_code == 200 and result.json()["terminal"] == "CLOSED"
        execution_before = state(job)
        offline = job[1].materials.with_name("offline-materials"); job[1].materials.rename(offline)
        result = client.post(BASE + "retire", json=body, headers=headers(config))
        assert result.status_code == 200 and result.json()["status"] == "REMOVED"
        receipt = result.json()
        assert receipt["proof_sha256"] == body["proof_sha256"] and receipt["retirement_id"] == body["retirement_id"]
        assert result.headers["cache-control"] == "no-store" and result.headers["x-content-type-options"] == "nosniff"
        assert config.token not in result.text and "raw_content" not in result.text
    with TestClient(create_packaging_app(config)) as client:
        assert client.post(BASE + "retire", json=body, headers=headers(config)).json() == receipt
        item = completed["stored"]["payload"]["files"][0]
        result = client.post(BASE + "artifact", json={"operation_id": body["request"]["operation_id"],
            "request_hash": body["request_hash"], "plan_hash": body["request"]["plan_hash"],
            "proof_sha256": body["proof_sha256"], **item}, headers=headers(config))
        assert result.status_code == 409 and result.json()["detail"]["code"] == "PACKAGING_STORE_RETIRED"
    assert state(job) == execution_before and not list(job[1].workspace.iterdir())
    assert snapshot(offline / job[2].parts[-1]) == source


def test_lost_retirement_reply_recovers_same_receipt_without_another_execution(job, monkeypatch):
    config = replace(settings(job), retirement_enabled=True)
    with TestClient(create_packaging_app(config), raise_server_exceptions=False) as client:
        completed = client.post(BASE + "dispatch", json=payload(job), headers=headers(config)).json()
        body = removal(job, completed["stored"]["proof_sha256"]); before = state(job)
        actual = retirement.retire_execution
        def lost(*args, **kwargs):
            actual(*args, **kwargs)
            raise ConnectionError("SYNTHETIC_PRIVATE_DIAGNOSTIC")
        with monkeypatch.context() as patch:
            patch.setattr(retirement, "retire_execution", lost)
            result = client.post(BASE + "retire", json=body, headers=headers(config))
            assert result.status_code == 500 and "PRIVATE_DIAGNOSTIC" not in result.text
        recovered = client.post(BASE + "retire", json=body, headers=headers(config))
        assert recovered.status_code == 200 and recovered.json()["status"] == "REMOVED"
        assert client.post(BASE + "retire", json=body, headers=headers(config)).json() == recovered.json()
        assert state(job) == before


@pytest.mark.parametrize("gate", ["service-slot", "execution-lease"])
def test_busy_runtime_or_execution_lease_prevents_retirement_intent(job, gate):
    config = replace(settings(job), retirement_enabled=True); app = create_packaging_app(config)
    with TestClient(app) as client:
        completed = client.post(BASE + "dispatch", json=payload(job), headers=headers(config)).json()
        body = removal(job, completed["stored"]["proof_sha256"]); before = snapshot(job[1].artifacts)
        if gate == "service-slot":
            assert app.state.execution_slot.acquire(blocking=False)
            try: result = client.post(BASE + "retire", json=body, headers=headers(config))
            finally: app.state.execution_slot.release()
            expected = "PACKAGING_SERVICE_BUSY"
        else:
            import fcntl
            lock = job[1].journal / str(job[2].operation_id) / "execution.lock"
            fd = os.open(lock, os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = client.post(BASE + "retire", json=body, headers=headers(config))
            finally: os.close(fd)
            expected = "PACKAGING_LEASE_BUSY"
        assert result.status_code == 503 and result.json()["detail"]["code"] == expected
        assert snapshot(job[1].artifacts) == before


@pytest.mark.parametrize("change", ["key", "proof", "request", "extra-path", "extra-report"])
def test_invalid_retirement_input_never_creates_a_journal(job, change):
    config = replace(settings(job), retirement_enabled=True); body = removal(job)
    if change == "key": body["retirement_id"] = "SYNTHETIC_PRIVATE"
    elif change == "proof": body["proof_sha256"] = "SYNTHETIC_PRIVATE"
    elif change == "request": body["request_hash"] = "b" * 64
    elif change == "extra-path": body["path"] = "SYNTHETIC_PRIVATE"
    else: body["report"] = {"synthetic": "private"}
    with TestClient(create_packaging_app(config)) as client:
        result = client.post(BASE + "retire", content=json.dumps(body), headers={**headers(config), "Content-Type": "application/json"})
        assert result.status_code == (409 if change == "request" else 422) and "SYNTHETIC_PRIVATE" not in result.text
    assert not list(job[1].journal.iterdir()) and not list(job[1].artifacts.iterdir())


@pytest.mark.parametrize("change,code", [
    ("legacy", "PACKAGING_DISPATCH_REQUIRED"), ("incomplete", "PACKAGING_RETIREMENT_NOT_READY"),
    ("proof", "PACKAGING_RETIREMENT_PROOF_MISMATCH"), ("workspace", "PACKAGING_EXECUTION_WORKSPACE_CHANGED"),
    ("root", "PACKAGING_EXECUTION_ROOT_CHANGED")])
def test_recorded_execution_guards_precede_any_retention_change(job, change, code):
    config = replace(settings(job), retirement_enabled=True)
    with TestClient(create_packaging_app(config)) as client:
        if change == "legacy":
            result = client.post(BASE + "execute", json={**prepared(job), "report": job[0][3]}, headers=headers(config))
        else:
            result = client.post(BASE + "dispatch", json=payload(job, "RECONCILE", 2) if change == "incomplete" else payload(job),
                headers=headers(config))
        assert result.status_code == 200
        stored = result.json()["stored"]
        body = removal(job, stored["proof_sha256"] if stored else "a" * 64)
        if change == "proof": body["proof_sha256"] = "b" * 64
        elif change == "workspace": (job[1].workspace / state(job)["attempts"][0]["workspace"]).mkdir(mode=0o700)
        elif change == "root":
            job[1].workspace.rename(job[1].workspace.with_name("original-workspace"))
            job[1].workspace.mkdir(mode=0o700)
        before, execution_before = snapshot(job[1].artifacts), state(job)
        denied = client.post(BASE + "retire", json=body, headers=headers(config))
        assert denied.status_code == 409 and denied.json()["detail"]["code"] == code
        assert snapshot(job[1].artifacts) == before and state(job) == execution_before


def test_another_retirement_key_cannot_replace_the_committed_intent(job):
    config = replace(settings(job), retirement_enabled=True)
    with TestClient(create_packaging_app(config)) as client:
        completed = client.post(BASE + "dispatch", json=payload(job), headers=headers(config)).json()
        body = removal(job, completed["stored"]["proof_sha256"])
        receipt = client.post(BASE + "retire", json=body, headers=headers(config)).json()
        result = client.post(BASE + "retire", json={**body, "retirement_id": str(uuid4())}, headers=headers(config))
        assert result.status_code == 409 and result.json()["detail"]["code"] == "PACKAGING_RETIREMENT_REQUEST_CONFLICT"
        assert client.post(BASE + "retire", json=body, headers=headers(config)).json() == receipt


@pytest.mark.parametrize("action,ordinal", [("EXECUTE", 1), ("RETRY", 2), ("RECONCILE", 2), ("CLOSE", 2)])
@pytest.mark.parametrize("interrupted", [False, True])
def test_late_dispatch_cannot_change_retired_history_or_block_receipt_recovery(job, monkeypatch, action, ordinal, interrupted):
    from app import packaging_retirement as storage_retirement
    config = replace(settings(job), retirement_enabled=True)
    with TestClient(create_packaging_app(config)) as client:
        completed = client.post(BASE + "dispatch", json=payload(job), headers=headers(config)).json()
        body = removal(job, completed["stored"]["proof_sha256"]); before = state(job)
        with monkeypatch.context() as patch:
            if interrupted:
                def crash(*args, **kwargs): raise OSError("Synthetic interruption after durable retirement intent")
                patch.setattr(storage_retirement, "_remove", crash)
            response = client.post(BASE + "retire", json=body, headers=headers(config))
            assert response.status_code == (409 if interrupted else 200)
        denied = client.post(BASE + "dispatch", json=payload(job, action, ordinal), headers=headers(config))
        assert denied.status_code == 409 and denied.json()["detail"]["code"] == "PACKAGING_STORE_RETIRED"
        assert state(job) == before
        recovered = client.post(BASE + "retire", json=body, headers=headers(config))
        assert recovered.status_code == 200 and recovered.json()["status"] == "REMOVED"
        assert state(job) == before and not list(job[1].workspace.iterdir())
