"""Actual retained bytes, strict HTTP binding and interrupted transfer cleanup."""
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from threading import BoundedSemaphore

import pytest
from fastapi.testclient import TestClient

from app import packaging_download as download
from app.packaging_api import ArtifactInput, create_packaging_app
from app.packaging_store import PackagingStoreError, open_retained_file
from test_packaging_execution import job, run, state
from test_packaging_api import BASE, packaging_runtime, settings, headers
from test_packaging_dispatch_api import payload

pytestmark = pytest.mark.usefixtures("packaging_runtime")


def selection(job, stored, item=None):
    item = item or stored.payload["files"][0]
    return dict(operation_id=str(job[2].operation_id), request_hash=job[2].sha256,
        plan_hash=stored.plan_hash, proof_sha256=stored.proof_sha256, **item)


def open_arguments(job, chosen):
    return dict(artifact_root=job[1].artifacts, operation_id=job[2].operation_id,
        request_hash=chosen["request_hash"], plan_hash=chosen["plan_hash"],
        expected_proof_sha256=chosen["proof_sha256"], path=chosen["path"])


def test_actual_http_files_survive_restart_offline_sources_and_ordered_closure(job):
    config = settings(job)
    with TestClient(create_packaging_app(config)) as client:
        result = client.post(BASE + "dispatch", json=payload(job), headers=headers(config))
        assert result.status_code == 200
        stored = result.json()["stored"]
        response = client.post(BASE + "dispatch", json=payload(job, "CLOSE", 2), headers=headers(config))
        assert response.status_code == 200 and response.json()["terminal"] == "CLOSED"
    job[1].materials.rename(job[1].materials.with_name("offline"))
    journal_before = state(job)
    with TestClient(create_packaging_app(config)) as client:
        for item in stored["payload"]["files"]:
            body = dict(operation_id=str(job[2].operation_id), request_hash=job[2].sha256,
                plan_hash=stored["plan_hash"], proof_sha256=stored["proof_sha256"], **item)
            response = client.post(BASE + "artifact", json=body, headers=headers(config))
            assert response.status_code == 200
            assert len(response.content) == item["size"] and hashlib.sha256(response.content).hexdigest() == item["sha256"]
            assert response.headers["content-type"] == "application/octet-stream"
            assert response.headers["x-packaging-proof-sha256"] == stored["proof_sha256"]
            assert response.headers["x-packaging-file-sha256"] == item["sha256"]
            assert response.headers["content-disposition"] == 'attachment; filename="package.bin"'
            assert "no-store" in response.headers["cache-control"]
            assert "nosniff" in response.headers["x-content-type-options"]
            assert config.token.encode() not in response.content
    assert state(job) == journal_before


@pytest.mark.parametrize("change", ["proof", "request", "plan", "size", "hash", "case", "unknown", "traversal", "absolute", "bool", "extra", "range"])
def test_wrong_binding_or_path_never_discloses_artifact_bytes(job, change):
    stored = run(job).stored
    body = selection(job, stored)
    if change in {"proof", "request", "plan"}: body[{"proof":"proof_sha256", "request":"request_hash", "plan":"plan_hash"}[change]] = "b" * 64
    elif change == "size": body["size"] += 1
    elif change == "hash": body["sha256"] = "b" * 64
    elif change == "case": body["path"] = body["path"].upper()
    elif change == "unknown": body["path"] = "PRIVATE.txt"
    elif change == "traversal": body["path"] = "../PRIVATE"
    elif change == "absolute": body["path"] = "/PRIVATE"
    elif change == "bool": body["size"] = True
    elif change == "extra": body["PRIVATE"] = "PRIVATE"
    config = settings(job)
    with TestClient(create_packaging_app(config)) as client:
        response = client.post(BASE + "artifact", json=body, headers={**headers(config), **({"Range":"bytes=0-5"} if change == "range" else {})})
        assert response.status_code in {404, 409, 422}
        assert response.headers["content-type"] == "application/json"
        assert "PRIVATE" not in response.text and config.token not in response.text


def test_authentication_and_service_slot_precede_any_file_access(job, monkeypatch):
    stored = run(job).stored
    config = settings(job); application = create_packaging_app(config)
    def forbidden(*args, **kwargs): pytest.fail("Rejected request must not open artifacts")
    monkeypatch.setattr(download, "open_retained_file", forbidden)
    with TestClient(application) as client:
        assert client.post(BASE + "artifact", content=b"PRIVATE").status_code == 401
        application.state.execution_slot.acquire()
        try:
            response = client.post(BASE + "artifact", json=selection(job, stored), headers=headers(config))
            assert response.status_code == 503 and response.json()["detail"]["code"] == "PACKAGING_SERVICE_BUSY"
        finally: application.state.execution_slot.release()


@pytest.mark.parametrize("change", ["corrupt", "symlink", "missing"])
def test_changed_retained_file_fails_before_binary_response(job, change):
    stored = run(job).stored; body = selection(job, stored)
    path = job[1].artifacts / str(job[2].operation_id) / "ready" / body["path"]
    if change == "corrupt":
        os.chmod(path, 0o600); path.write_bytes(b"x" * body["size"]); os.chmod(path, 0o400)
    else:
        path.unlink()
        if change == "symlink": path.symlink_to("/PRIVATE")
    config = settings(job)
    with TestClient(create_packaging_app(config)) as client:
        response = client.post(BASE + "artifact", json=body, headers=headers(config))
        assert response.status_code in {409, 503} and response.headers["content-type"] == "application/json"
        assert "PRIVATE" not in response.text


async def invoke(response, send, *, disconnect=False):
    emitted = False
    async def receive():
        nonlocal emitted
        if disconnect and not emitted:
            emitted = True
            return {"type": "http.disconnect"}
        await asyncio.Future()
    await response({"type":"http", "method":"POST", "asgi":{"spec_version":"2.0" if disconnect else "2.4"}}, receive, send)


@pytest.mark.parametrize("failure", ["disconnect", "send", "timeout", "digest", "replacement"])
def test_interrupted_stream_closes_real_descriptor_and_releases_both_leases(job, monkeypatch, failure):
    stored = run(job).stored; body = selection(job, stored)
    slot = BoundedSemaphore(1); recorded = []; total = 0; complete = False
    original = download.open_retained_file
    @contextmanager
    def observed(**kwargs):
        with original(**kwargs) as fd:
            recorded.append(fd)
            yield fd
    monkeypatch.setattr(download, "open_retained_file", observed)
    monkeypatch.setattr(download, "CHUNK_BYTES", 8)
    if failure == "timeout": monkeypatch.setattr(download, "TRANSFER_SECONDS", 0.05)
    if failure == "digest":
        original_read = os.read
        def corrupt(fd, size):
            chunk = original_read(fd, size)
            return b"!" + chunk[1:] if chunk and fd in recorded else chunk
        monkeypatch.setattr(download.os, "read", corrupt)
    async def send(message):
        nonlocal total, complete
        if failure == "timeout": await asyncio.sleep(1)
        if message["type"] == "http.response.body":
            if failure == "send": raise OSError("PRIVATE")
            if failure == "replacement" and total == 0:
                target = job[1].artifacts / str(job[2].operation_id) / "ready" / body["path"]
                copied = target.read_bytes(); target.unlink(); target.write_bytes(copied); os.chmod(target, 0o400)
            total += len(message.get("body", b""))
            complete = not message.get("more_body", False)
    response = download.RetainedFileResponse(slot=slot, selection=ArtifactInput(**body), artifact_root=job[1].artifacts)
    try: asyncio.run(invoke(response, send, disconnect=failure == "disconnect"))
    except (RuntimeError, PackagingStoreError, OSError) as error: assert "PRIVATE" not in str(error)
    assert not complete and total < body["size"]
    assert slot.acquire(blocking=False)
    slot.release()
    assert recorded
    for fd in recorded:
        with pytest.raises(OSError): os.fstat(fd)
    # A new opener can acquire the operation lock; replacement itself remains
    # rejected, but no interrupted request can leave PACKAGING_STORE_BUSY behind.
    try:
        with open_retained_file(**open_arguments(job, body)): pass
    except PackagingStoreError as error: assert str(error) != "PACKAGING_STORE_BUSY"


def test_memory_stays_chunk_bounded_and_success_checks_final_identity_before_last_byte(job, monkeypatch):
    stored = run(job).stored; body = selection(job, stored)
    slot = BoundedSemaphore(1); closed = False; emitted = []; original = download.open_retained_file
    @contextmanager
    def observed(**kwargs):
        nonlocal closed
        with original(**kwargs) as fd: yield fd
        closed = True
    monkeypatch.setattr(download, "open_retained_file", observed)
    monkeypatch.setattr(download, "CHUNK_BYTES", 8)
    async def send(message):
        if message["type"] == "http.response.body":
            chunk = message.get("body", b""); assert len(chunk) <= 8
            emitted.append(chunk)
            if sum(map(len, emitted)) == body["size"]: assert closed
    asyncio.run(invoke(download.RetainedFileResponse(slot=slot, selection=ArtifactInput(**body), artifact_root=job[1].artifacts), send))
    assert hashlib.sha256(b"".join(emitted)).hexdigest() == body["sha256"]
    assert slot.acquire(blocking=False)
