"""Ordered private HTTP commands against real synthetic retained artifacts."""
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.packaging_api import create_packaging_app
from test_packaging_execution import job, state
from test_packaging_api import BASE, packaging_runtime, settings, headers, prepared


def payload(job, action="EXECUTE", ordinal=1):
    return {**prepared(job), "dispatch": {"id": str(uuid4()), "action": action, "ordinal": ordinal},
        **({"report": job[0][3]} if action in {"EXECUTE", "RETRY"} else {})}


@pytest.mark.usefixtures("packaging_runtime")
def test_actual_ordered_http_execution_closure_and_old_entry_point_cannot_restart(job):
    config = settings(job); first = payload(job)
    with TestClient(create_packaging_app(config)) as client:
        created = client.post(BASE + "dispatch", json=first, headers=headers(config))
        assert created.status_code == 200
        body = created.json()
        assert body["status"] == "READY" and body["dispatch"] == first["dispatch"] and body["terminal"] == "OPEN"
        assert client.post(BASE + "dispatch", json=first, headers=headers(config)).json() == body
        job[1].materials.rename(job[1].materials.with_name("offline"))
        closing = payload(job, "CLOSE", 2)
        result = client.post(BASE + "dispatch", json=closing, headers=headers(config))
        assert result.status_code == 200 and result.json()["terminal"] == "CLOSED"
        assert result.json()["stored"] == body["stored"]
        assert client.post(BASE + "dispatch", json=closing, headers=headers(config)).json() == result.json()
        stale = client.post(BASE + "dispatch", json=first, headers=headers(config))
        assert stale.status_code == 409 and stale.json()["detail"]["code"] == "PACKAGING_DISPATCH_STALE"
        rejected = client.post(BASE + "dispatch", json=payload(job, "RETRY", 3), headers=headers(config))
        assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "PACKAGING_EXECUTION_CLOSED"
        old = client.post(BASE + "execute", json={**prepared(job), "report": job[0][3], "retry": True}, headers=headers(config))
        assert old.status_code == 409 and old.json()["detail"]["code"] == "PACKAGING_DISPATCH_REQUIRED"
        assert len(state(job)["attempts"]) == 1 and config.token not in result.text


@pytest.mark.usefixtures("packaging_runtime")
def test_http_reconcile_missing_execution_persists_a_fence_and_can_close_with_nas_offline(job):
    config = settings(job)
    job[1].materials.rename(job[1].materials.with_name("offline"))
    with TestClient(create_packaging_app(config)) as client:
        recovered = client.post(BASE + "dispatch", json=payload(job, "RECONCILE", 2), headers=headers(config))
        assert recovered.status_code == 200 and recovered.json()["status"] == "RETRY_REQUIRED"
        closed = client.post(BASE + "dispatch", json=payload(job, "CLOSE", 3), headers=headers(config))
        assert closed.status_code == 200 and closed.json()["terminal"] == "CLOSED"
        assert not list(job[1].workspace.iterdir()) and not list(job[1].artifacts.iterdir())


@pytest.mark.usefixtures("packaging_runtime")
@pytest.mark.parametrize("change", ["missing", "ordinal", "id", "action", "execute-order", "missing-report", "unexpected-report"])
def test_invalid_ordered_request_is_rejected_before_journal_creation(job, change):
    config = settings(job); body = payload(job)
    if change == "missing": body.pop("dispatch")
    elif change == "ordinal": body["dispatch"]["ordinal"] = True
    elif change == "id": body["dispatch"]["id"] = "PRIVATE_INVALID"
    elif change == "action": body["dispatch"]["action"] = "PRIVATE_INVALID"
    elif change == "execute-order": body["dispatch"]["ordinal"] = 2
    elif change == "missing-report": body.pop("report")
    else: body["dispatch"].update(action="CLOSE", ordinal=2)
    with TestClient(create_packaging_app(config)) as client:
        response = client.post(BASE + "dispatch", content=json.dumps(body).encode(),
            headers={**headers(config), "Content-Type": "application/json"})
        assert response.status_code in {409, 422} and "PRIVATE_INVALID" not in response.text
        assert not list(job[1].journal.iterdir())
