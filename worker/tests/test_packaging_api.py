import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app import packaging_api as api
from app.packaging_api import PackagingServiceSettings, create_packaging_app
from app.packaging_convert import EXECUTABLE
from test_packaging_execution import job, state

BASE = "/internal/packaging/"


@pytest.fixture
def packaging_runtime():
    if not Path(EXECUTABLE).is_file():
        if os.environ.get("REQUIRE_PACKAGING_RUNTIME") == "1": pytest.fail("Required packaging runtime is absent")
        pytest.skip("Opt-in Linux ImageMagick Q16-HDRI runtime required")


def settings(job):
    return PackagingServiceSettings(True, uuid4().hex + uuid4().hex, job[1])


def headers(config): return {"Authorization": "Bearer " + config.token}


def prepared(job): return {"request": job[2].document(), "request_hash": job[2].sha256}


def initial(job):
    request = job[2]
    return {"operation_id": str(request.operation_id), "parts": list(request.parts),
        "expected_source_revision_hash": request.source_revision_hash, "expected_technical_report_hash": request.technical_report_hash,
        "approval_context_hash": request.approval_context_hash, "policy": request.policy, "storage_timezone": request.storage_timezone,
        "report": job[0][3], "limits": request.document()["limits"]}


@pytest.mark.parametrize("config", [PackagingServiceSettings(), PackagingServiceSettings(True),
    PackagingServiceSettings(True, "short"), PackagingServiceSettings(False, uuid4().hex)])
def test_incomplete_service_is_closed_without_reflecting_inputs(config):
    with TestClient(create_packaging_app(config)) as client:
        response = client.post(BASE + "execute", content=b"SYNTHETIC_PRIVATE")
        assert response.status_code == 503 and response.json()["detail"]["code"] == "PACKAGING_SERVICE_DISABLED"
        assert "PRIVATE" not in response.text
        assert client.get("/health").status_code == 503
        assert client.get("/health").json() == {"service": "packaging", "status": "disabled"}


def test_environment_does_not_enable_packaging_or_reuse_mutation_credential(monkeypatch):
    for name in ("PACKAGING_ENABLED", "PACKAGING_SERVICE_TOKEN", "PACKAGING_WORKSPACE_ROOT", "PACKAGING_ARTIFACT_ROOT", "PACKAGING_JOURNAL_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SOURCE_MUTATIONS_ENABLED", "true")
    monkeypatch.setenv("WORKER_MUTATION_TOKEN", uuid4().hex)
    assert not PackagingServiceSettings.from_environment().configured()
    assert "token=" not in repr(PackagingServiceSettings(True, uuid4().hex))


@pytest.mark.usefixtures("packaging_runtime")
def test_authorized_actual_prepare_execute_reconcile_and_offline_exact_replay(job):
    config = settings(job)
    with TestClient(create_packaging_app(config)) as client:
        assert client.get("/health").json() == {"service": "packaging", "status": "ready"}
        response = client.post(BASE + "prepare", json=initial(job), headers=headers(config))
        assert response.status_code == 200 and response.json() == prepared(job)
        assert not list(job[1].journal.iterdir()) and not list(job[1].artifacts.iterdir())
        response = client.post(BASE + "execute", json={**prepared(job), "report": job[0][3]}, headers=headers(config))
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "READY" and body["attempt"] == 1 and body["stored"]["proof_sha256"] == state(job)["result"]["proof_sha256"]
        assert "raw_content" not in response.text and config.token not in response.text
        assert response.headers["cache-control"] == "no-store"
        job[1].materials.rename(job[1].materials.with_name("offline"))
        response = client.post(BASE + "reconcile", json=prepared(job), headers=headers(config))
        assert response.status_code == 200 and response.json() == body
        assert client.post(BASE + "execute", json={**prepared(job), "report": job[0][3], "retry": True}, headers=headers(config)).json() == body


@pytest.mark.usefixtures("packaging_runtime")
def test_lost_committed_response_replays_original_result_without_new_attempt(job, monkeypatch):
    config = settings(job); real = api._response; lost = True
    def response(result):
        nonlocal lost
        if lost:
            lost = False
            raise ConnectionError("Synthetic lost response")
        return real(result)
    monkeypatch.setattr(api, "_response", response)
    with TestClient(create_packaging_app(config), raise_server_exceptions=False) as client:
        payload = {**prepared(job), "report": job[0][3]}
        first = client.post(BASE + "execute", json=payload, headers=headers(config))
        assert first.status_code == 500 and state(job)["status"] == "READY"
        proof = state(job)["result"]["proof_sha256"]
        job[1].materials.rename(job[1].materials.with_name("offline"))
        second = client.post(BASE + "execute", json=payload, headers=headers(config))
        assert second.status_code == 200 and second.json()["stored"]["proof_sha256"] == proof and second.json()["attempt"] == 1


@pytest.mark.usefixtures("packaging_runtime")
@pytest.mark.parametrize("credential", ["missing", "wrong", "duplicate", "cookie", "nonascii"])
def test_authentication_precedes_body_parsing_and_never_mutates(job, credential):
    config = settings(job); auth = []
    if credential == "wrong": auth = [("Authorization", "Bearer " + uuid4().hex)]
    if credential == "duplicate": auth = list(headers(config).items()) * 2
    if credential == "cookie": auth = [("Cookie", "session=" + config.token)]
    if credential == "nonascii": auth = [(b"authorization", b"Bearer \xff")]
    with TestClient(create_packaging_app(config)) as client:
        response = client.post(BASE + "execute", content=b"SYNTHETIC_PRIVATE_MALFORMED", headers=auth)
        assert response.status_code == 401 and response.json()["detail"]["code"] == "PACKAGING_SERVICE_UNAUTHORIZED"
        assert "PRIVATE" not in response.text and config.token not in response.text
    assert not list(job[1].journal.iterdir())


@pytest.mark.usefixtures("packaging_runtime")
@pytest.mark.parametrize("change", ["path", "unknown-field", "hash", "report-extra", "numeric-string", "nan", "version-bool", "operation", "limits", "retry"])
def test_invalid_or_conflicting_requests_do_not_reserve_work_or_echo_input(job, change):
    config = settings(job); payload = json.loads(json.dumps({**prepared(job), "report": job[0][3]}))
    if change == "path": payload["request"]["parts"] = ["..", "SYNTHETIC_PRIVATE"]
    elif change == "unknown-field": payload["raw_metadata"] = "SYNTHETIC_PRIVATE"
    elif change == "hash": payload["request_hash"] = "b" * 64
    elif change == "report-extra": payload["report"]["raw_metadata"] = "SYNTHETIC_PRIVATE"
    elif change == "numeric-string": payload["report"]["images"][0]["width"] = "1024"
    elif change == "nan": payload["report"]["images"][0]["width"] = float("nan")
    elif change == "version-bool": payload["request"]["version"] = True
    elif change == "operation": payload["request"]["operation_id"] = "SYNTHETIC_PRIVATE"
    elif change == "limits": payload["request"]["limits"]["seconds"] = 3601
    elif change == "retry": payload["retry"] = "true"
    with TestClient(create_packaging_app(config)) as client:
        response = client.post(BASE + "execute", content=json.dumps(payload).encode(), headers={**headers(config), "Content-Type": "application/json"})
        assert response.status_code in {409, 422}
        assert "PRIVATE" not in response.text and config.token not in response.text
    assert not list(job[1].journal.iterdir()) and not list(job[1].artifacts.iterdir())


@pytest.mark.usefixtures("packaging_runtime")
def test_concurrency_slot_is_nonblocking_and_released_after_errors(job):
    config = settings(job); application = create_packaging_app(config)
    with TestClient(application) as client:
        application.state.execution_slot.acquire()
        try:
            result = client.post(BASE + "prepare", json=initial(job), headers=headers(config))
            assert result.status_code == 503 and result.json()["detail"]["code"] == "PACKAGING_SERVICE_BUSY"
            assert result.headers["retry-after"] == "1"
        finally: application.state.execution_slot.release()
        bad = initial(job); bad["expected_technical_report_hash"] = "b" * 64
        assert client.post(BASE + "prepare", json=bad, headers=headers(config)).status_code == 409
        assert client.post(BASE + "prepare", json=initial(job), headers=headers(config)).status_code == 200
    assert not list(job[1].journal.iterdir())


def test_runtime_or_private_root_failure_does_not_enable_service(job, monkeypatch):
    config = settings(job)
    def unavailable(): raise api.PackagingConversionError("PACKAGING_CONVERSION_RUNTIME_UNAVAILABLE")
    monkeypatch.setattr(api, "verify_runtime", unavailable)
    with TestClient(create_packaging_app(config)) as client:
        assert client.get("/health").status_code == 503
        result = client.post(BASE + "execute", json={}, headers=headers(config))
        assert result.status_code == 503 and result.json()["detail"]["code"] == "PACKAGING_SERVICE_UNAVAILABLE"
    assert not list(job[1].journal.iterdir())


async def direct(application, config, chunks, *, extra=(), with_auth=True, forbidden=False):
    messages = []; calls = 0
    async def receive():
        nonlocal calls
        assert not forbidden
        calls += 1
        return {"type": "http.request", "body": chunks[calls - 1], "more_body": calls < len(chunks)}
    async def send(message): messages.append(message)
    raw_headers = [(b"content-type", b"application/json"), *extra]
    if with_auth: raw_headers.append((b"authorization", ("Bearer " + config.token).encode()))
    scope = {"type": "http", "http_version": "1.1", "method": "POST", "scheme": "http", "path": BASE + "execute",
        "raw_path": (BASE + "execute").encode(), "query_string": b"", "headers": raw_headers,
        "server": ("test", 80), "client": ("test", 1234), "root_path": ""}
    await application(scope, receive, send)
    return messages, calls


@pytest.mark.parametrize("case", ["unauthorized", "too-large-declared", "too-large-streamed", "length-mismatch", "duplicate-length"])
def test_asgi_boundary_limits_streams_and_authenticates_before_consuming_body(job, monkeypatch, case):
    config = settings(job); application = create_packaging_app(config)
    # Boundary-only tests do not initialize the image runtime or invoke execution.
    application.state.available = True
    monkeypatch.setattr(api, "MAX_BODY_BYTES", 128)
    extra = (); chunks = [b"x" * 80, b"x" * 80]; forbidden = False; authorized = True
    if case == "unauthorized": authorized = False; forbidden = True; status = 401
    elif case == "too-large-declared": extra = ((b"content-length", b"129"),); forbidden = True; status = 413
    elif case == "too-large-streamed": status = 413
    elif case == "length-mismatch": extra = ((b"content-length", b"10"),); chunks = [b"{}"]; status = 422
    else: extra = ((b"content-length", b"1"), (b"content-length", b"1")); forbidden = True; status = 413
    messages, calls = asyncio.run(direct(application, config, chunks, extra=extra, with_auth=authorized, forbidden=forbidden))
    assert messages[0]["type"] == "http.response.start" and messages[0]["status"] == status
    assert calls == (0 if forbidden else len(chunks)) and not list(job[1].journal.iterdir())
