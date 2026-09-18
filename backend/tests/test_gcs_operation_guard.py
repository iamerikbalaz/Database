"""Revocation at real transport boundaries, with synthetic offline HTTP only."""
import asyncio
from contextlib import aclosing

import anyio
import httpx
import pytest

from app import gcs_client as transport
from app.gcs_contract import GcsError
from test_gcs_client import DATA, Stream, install, token


@pytest.mark.parametrize("boundary", ["initial", "credential", "session", "chunk", "source-eof", "completed-object", "final-metadata"])
def test_revoked_operation_stops_subsequent_requests_and_never_returns_receipt(monkeypatch, boundary):
    data = b"x" * (transport.UPLOAD_CHUNK_BYTES + 1) if boundary == "chunk" else DATA
    client, storage = install(monkeypatch, data=data)
    revoked = boundary == "initial"
    credential_reads = 0

    async def provider():
        nonlocal revoked, credential_reads
        credential_reads += 1
        if boundary == "credential": revoked = True
        return await token()

    async def guard():
        if revoked:
            raise RuntimeError("SYNTHETIC-PRIVATE-LEASE-OR-SESSION-ERROR")

    def hook(request):
        nonlocal revoked
        if ((boundary == "session" and request.method == "POST")
                or (boundary in {"chunk", "completed-object"} and request.method == "PUT")
                or (boundary == "final-metadata" and request.method == "GET" and request.url.params.get("alt") != "media")):
            revoked = True

    async def source():
        nonlocal revoked
        async for block in storage.source(): yield block
        if boundary == "source-eof": revoked = True

    async def run():
        async with aclosing(source()) as selected:
            await client.upload(storage.spec, selected, operation_guard=guard)

    client._token_provider = provider; storage.hook = hook
    with pytest.raises(GcsError, match="^GCS_OPERATION_BLOCKED$") as caught:
        asyncio.run(run())
    assert "PRIVATE" not in str(caught.value)
    assert all(stream.closed for stream in storage.streams)
    if boundary == "initial": assert credential_reads == 0
    if boundary in {"initial", "credential"}: assert not storage.requests
    elif boundary in {"session", "source-eof"}: assert [r.method for r in storage.requests] == ["POST"]
    elif boundary in {"chunk", "completed-object"}: assert [r.method for r in storage.requests] == ["POST", "PUT"]
    else: assert [r.method for r in storage.requests] == ["POST", "PUT", "GET", "GET"]
    assert storage.complete is (boundary in {"completed-object", "final-metadata"})
    # Guard state is per operation; release the slot and do not inherit a revoked
    # closure into later, explicitly authorized read-only reconciliation.
    storage.hook = None; storage.complete = True
    assert asyncio.run(client.reconcile(storage.spec)).spec == storage.spec


@pytest.mark.parametrize("mode", ["bytes", "elapsed"])
def test_readback_checks_revocation_during_stream_and_closes_it(monkeypatch, mode):
    client, storage = install(monkeypatch)
    storage.complete = True
    revoked = False
    if mode == "bytes": monkeypatch.setattr(transport, "RECHECK_BYTES", 1)
    else: monkeypatch.setattr(transport, "RECHECK_SECONDS", 0)

    async def guard():
        if revoked: raise RuntimeError("Synthetic operator lost access")

    class RevokingStream(Stream):
        async def __aiter__(self):
            nonlocal revoked
            revoked = True
            yield self.data

    def hook(request):
        if request.url.params.get("alt") == "media":
            stream = RevokingStream(DATA); storage.streams.append(stream)
            return httpx.Response(200, headers={"content-length": str(len(DATA))}, stream=stream)

    storage.hook = hook
    with pytest.raises(GcsError, match="^GCS_OPERATION_BLOCKED$"):
        asyncio.run(client.reconcile(storage.spec, operation_guard=guard))
    assert len(storage.requests) == 2 and all(stream.closed for stream in storage.streams)
    assert all(request.method == "GET" for request in storage.requests)


@pytest.mark.parametrize("mode", ["bytes", "elapsed"])
def test_source_progress_checks_revocation_before_further_cloud_writes(monkeypatch, mode):
    client, storage = install(monkeypatch)
    revoked = False
    if mode == "bytes": monkeypatch.setattr(transport, "RECHECK_BYTES", 1)
    else: monkeypatch.setattr(transport, "RECHECK_SECONDS", 0)

    async def guard():
        if revoked: raise RuntimeError("Synthetic lease lost")

    async def source():
        nonlocal revoked
        revoked = True
        yield DATA
        pytest.fail("Revoked source continued reading")

    async def run():
        async with aclosing(source()) as selected:
            await client.upload(storage.spec, selected, operation_guard=guard)

    with pytest.raises(GcsError, match="^GCS_OPERATION_BLOCKED$"): asyncio.run(run())
    assert [request.method for request in storage.requests] == ["POST"]
    assert all(stream.closed for stream in storage.streams)


@pytest.mark.parametrize("value", [False, True, "authorized", 1])
def test_guard_must_complete_without_a_returned_decision_value(monkeypatch, value):
    client, storage = install(monkeypatch)
    async def guard(): return value
    with pytest.raises(GcsError, match="^GCS_OPERATION_BLOCKED$"):
        asyncio.run(client.reconcile(storage.spec, operation_guard=guard))
    assert not storage.requests


@pytest.mark.parametrize("guard", [False, "not-callable", lambda: None])
def test_invalid_or_non_async_guard_fails_before_cloud_io(monkeypatch, guard):
    client, storage = install(monkeypatch)
    with pytest.raises(GcsError, match="^GCS_OPERATION_BLOCKED$"):
        asyncio.run(client.reconcile(storage.spec, operation_guard=guard))
    assert not storage.requests


@pytest.mark.parametrize("interrupt", ["timeout", "cancel"])
def test_interrupted_authorization_releases_transport_slot(monkeypatch, interrupt):
    client, storage = install(monkeypatch, timeout_seconds=.03 if interrupt == "timeout" else 2.0)
    storage.complete = True
    async def run():
        entered = anyio.Event()
        async def guard():
            entered.set()
            await anyio.sleep_forever()
        async def attempt():
            await client.reconcile(storage.spec, operation_guard=guard)
        if interrupt == "timeout":
            with pytest.raises(GcsError, match="^GCS_OUTCOME_UNCERTAIN$"): await attempt()
        else:
            async with anyio.create_task_group() as group:
                group.start_soon(attempt)
                await entered.wait()
                group.cancel_scope.cancel()
        assert not storage.requests
        assert (await client.reconcile(storage.spec)).spec == storage.spec
    asyncio.run(run())


def test_successful_guard_allows_a_fully_verified_object(monkeypatch):
    client, storage = install(monkeypatch)
    checked = False
    async def guard():
        nonlocal checked
        checked = True
    async def run():
        async with aclosing(storage.source()) as source:
            return await client.upload(storage.spec, source, operation_guard=guard)
    receipt = asyncio.run(run())
    assert checked and receipt.spec == storage.spec and storage.complete
    assert all(stream.closed for stream in storage.streams)
