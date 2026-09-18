"""Synthetic JSON API contract tests. No network or real credentials are used."""
import asyncio
from contextlib import aclosing
import hashlib
import json
import logging
from urllib.parse import urlencode
from uuid import uuid4

import anyio
import httpx
from pydantic import SecretStr
import pytest

from app import gcs_client as transport
from app.core.config import Settings
from app.gcs_contract import GcsConfiguration, GcsError, GcsObjectSpec

DATA = b"Synthetic package bytes for isolated contract tests"
TOKEN = "synthetic-oauth-value-" + "x" * 32
SESSION_ID = "synthetic-private-resumable-session"
BUCKET = "synthetic-reawote-staging"


def spec(data=DATA, **changes):
    return GcsObjectSpec(job_id=str(uuid4()), binding_sha256="a" * 64,
        relative_path="materials/červená.zip", size=len(data),
        sha256=hashlib.sha256(data).hexdigest()).model_copy(update=changes)


def config(**changes):
    return GcsConfiguration(enabled=True, bucket_name=BUCKET,
        staging_prefix="isolated/contracts", **changes)


async def token():
    return SecretStr(TOKEN)


async def blocks(data=DATA, *, tail_error=False):
    for offset in range(0, len(data), transport.SOURCE_BLOCK_BYTES):
        yield data[offset:offset + transport.SOURCE_BLOCK_BYTES]
    if tail_error:
        raise RuntimeError("SYNTHETIC-PRIVATE-SOURCE-ERROR")


class Stream(httpx.AsyncByteStream):
    def __init__(self, data, *, wait=False):
        self.data = data
        self.closed = False
        self.reads = 0
        self.wait = wait

    async def __aiter__(self):
        if self.wait:
            await anyio.sleep(1)
        for offset in range(0, len(self.data), 4096):
            self.reads += 1
            yield self.data[offset:offset + 4096]

    async def aclose(self):
        await anyio.sleep(0)
        self.closed = True


class Storage:
    def __init__(self, selection, data=DATA):
        self.spec = selection
        self.data = data
        self.received = bytearray()
        self.requests = []
        self.streams = []
        self.complete = False
        self.hook = None
        self.eof = False

    def metadata(self):
        return {"kind": "storage#object", "bucket": BUCKET,
            "name": self.spec.object_name(config()), "size": str(self.spec.size),
            "generation": "12345678901234", "metageneration": "1",
            "metadata": self.spec.metadata(), "contentType": "application/octet-stream",
            "cacheControl": "no-store"}

    def response(self, status=200, *, body=b"", headers=None, value=None, wait=False):
        values = {}
        if value is not None:
            body = json.dumps(value).encode()
            values["content-type"] = "application/json; charset=UTF-8"
        values.update(headers or {})
        stream = Stream(body, wait=wait)
        self.streams.append(stream)
        return httpx.Response(status, headers=values, stream=stream)

    def session(self):
        return f"{transport.ORIGIN}/upload/storage/v1/b/{BUCKET}/o?" + urlencode({
            "uploadType": "resumable", "upload_id": SESSION_ID,
            "name": self.spec.object_name(config())})

    def handle(self, request):
        self.requests.append(request)
        assert request.url.scheme == "https" and request.url.host == "storage.googleapis.com"
        assert request.headers["authorization"] == "Bearer " + TOKEN
        assert request.headers["accept-encoding"] == "identity"
        if self.hook:
            overridden = self.hook(request)
            if overridden is not None:
                return overridden
        if request.method == "POST":
            assert dict(request.url.params) == {"uploadType": "resumable", "ifGenerationMatch": "0"}
            assert request.headers["x-upload-content-length"] == str(self.spec.size)
            assert json.loads(request.content) == {
                "name": self.spec.object_name(config()), "contentType": "application/octet-stream",
                "cacheControl": "no-store", "metadata": self.spec.metadata()}
            return self.response(headers={"location": self.session()})
        if request.method == "PUT":
            start = len(self.received)
            end = start + len(request.content) - 1
            assert request.headers["content-range"] == f"bytes {start}-{end}/{self.spec.size}"
            assert int(request.headers["content-length"]) == len(request.content)
            assert len(request.content) <= transport.UPLOAD_CHUNK_BYTES
            self.received.extend(request.content)
            if len(self.received) == self.spec.size:
                assert self.eof, "Final write must wait for EOF, not just the declared byte count"
                self.complete = True
                return self.response(201, value=self.metadata())
            assert len(request.content) % (256 * 1024) == 0
            return self.response(308, headers={"range": f"bytes=0-{end}"})
        assert request.method == "GET" and request.url.params.get("projection", "noAcl") == "noAcl"
        assert request.url.path.endswith(self.spec.object_name(config()))
        if not self.complete:
            return self.response(404, body=b"SYNTHETIC-PRIVATE-ERROR")
        if request.url.params.get("alt") == "media":
            assert dict(request.url.params) == {"alt": "media", "generation": "12345678901234",
                "ifGenerationMatch": "12345678901234", "ifMetagenerationMatch": "1"}
            return self.response(body=self.data, headers={"content-length": str(len(self.data))})
        return self.response(value=self.metadata())

    async def source(self, data=None, *, tail_error=False):
        async for chunk in blocks(self.data if data is None else data, tail_error=tail_error):
            yield chunk
        self.eof = True


def install(monkeypatch, selection=None, data=DATA, **configuration):
    storage = Storage(selection or spec(data), data)
    original = httpx.AsyncClient
    def connection(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        assert kwargs["http2"] is False and kwargs["timeout"].connect == 5
        return original(**kwargs, transport=httpx.MockTransport(storage.handle))
    monkeypatch.setattr(httpx, "AsyncClient", connection)
    return GcsClient(config(**configuration), token), storage


GcsClient = transport.GcsClient


def upload(client, storage, **kwargs):
    async def run():
        async with aclosing(storage.source(**kwargs)) as source:
            return await client.upload(storage.spec, source)
    return asyncio.run(run())


@pytest.mark.parametrize("size", [1, len(DATA), 8 * 1024**2, 8 * 1024**2 + 19, 16 * 1024**2 + 1])
def test_create_only_chunked_upload_then_full_generation_bound_readback(monkeypatch, size):
    data = (DATA * (size // len(DATA) + 1))[:size]
    client, storage = install(monkeypatch, data=data)
    receipt = upload(client, storage)
    assert receipt.spec == storage.spec and bytes(storage.received) == data
    assert receipt.generation == "12345678901234"
    assert [r.method for r in storage.requests] == ["POST"] + ["PUT"] * ((size - 1) // transport.UPLOAD_CHUNK_BYTES + 1) + ["GET", "GET"]
    assert "generation" not in storage.requests[-1].url.params
    assert storage.requests[-1].url.params["ifGenerationMatch"] == receipt.generation
    assert all(s.closed for s in storage.streams)


def test_lost_final_response_reconciles_read_only_with_a_new_client(monkeypatch):
    client, storage = install(monkeypatch)
    original = storage.handle
    def lost(request):
        response = original(request)
        if request.method == "PUT":
            raise httpx.ReadError("SYNTHETIC-PRIVATE-REMOTE-ERROR")
        return response
    storage.handle = lost
    # The mock callback bound during connection creation observes this replacement.
    with pytest.raises(GcsError, match="GCS_OUTCOME_UNCERTAIN"):
        upload(client, storage)
    assert storage.complete
    storage.handle = original
    storage.requests.clear()
    receipt = asyncio.run(GcsClient(config(), token).reconcile(storage.spec))
    assert receipt.spec == storage.spec
    assert [r.method for r in storage.requests] == ["GET", "GET", "GET"]


@pytest.mark.parametrize("mutation", ["missing", "different-job", "different-binding", "different-bytes", "changed-live-object"])
def test_reconcile_requires_metadata_bytes_and_still_current_generation(monkeypatch, mutation):
    client, storage = install(monkeypatch)
    storage.complete = mutation != "missing"
    def hook(request):
        if mutation == "different-bytes" and request.url.params.get("alt") == "media":
            return storage.response(body=b"x" * len(DATA), headers={"content-length": str(len(DATA))})
        if mutation == "changed-live-object" and len(storage.requests) == 3:
            return storage.response(412)
        if mutation in {"different-job", "different-binding"} and len(storage.requests) == 1:
            value = storage.metadata()
            value["metadata"]["reawote-job" if mutation == "different-job" else "reawote-binding-sha256"] = "b" * 64
            return storage.response(value=value)
    storage.hook = hook
    with pytest.raises(GcsError):
        asyncio.run(client.reconcile(storage.spec))
    assert all(r.method == "GET" for r in storage.requests)
    assert all(s.closed for s in storage.streams)


@pytest.mark.parametrize("kind", ["truncated", "overflow", "corrupt", "tail-error", "empty-block", "large-block", "mutable-block"])
def test_bad_source_never_sends_final_chunk(monkeypatch, kind):
    data = DATA * (transport.UPLOAD_CHUNK_BYTES // len(DATA) + 100)
    client, storage = install(monkeypatch, data=data)
    async def source():
        if kind in {"empty-block", "large-block", "mutable-block"}:
            yield b"" if kind == "empty-block" else b"x" * (transport.SOURCE_BLOCK_BYTES + 1) if kind == "large-block" else bytearray(b"x")
        else:
            changed = data[:-1] if kind == "truncated" else data + b"x" if kind == "overflow" else b"x" + data[1:] if kind == "corrupt" else data
            async for block in blocks(changed, tail_error=kind == "tail-error"):
                yield block
    with pytest.raises(GcsError, match="GCS_SOURCE_"):
        asyncio.run(client.upload(storage.spec, source()))
    assert not storage.complete and len(storage.received) < len(data)
    assert all(s.closed for s in storage.streams)


@pytest.mark.parametrize("location", [
    "http://storage.googleapis.com/upload/storage/v1/b/synthetic-reawote-staging/o?uploadType=resumable&upload_id=x",
    "https://evil.example/", "https://storage.googleapis.com.evil.example/",
    "https://user@storage.googleapis.com/", "https://storage.googleapis.com:443/",
    "https://storage.googleapis.com/download/", None,
    "https://storage.googleapis.com/upload/storage/v1/b/other-bucket/o?uploadType=resumable&upload_id=x",
    "https://storage.googleapis.com/upload/storage/v1/b/synthetic-reawote-staging/o?uploadType=resumable&upload_id=x&name=wrong",
    "https://storage.googleapis.com/upload/storage/v1/b/synthetic-reawote-staging/o?uploadType=resumable&upload_id=x&upload_id=y",
    "https://storage.googleapis.com/upload/storage/v1/b/synthetic-reawote-staging/o?uploadType=resumable&upload_id=x&ifGenerationMatch=12",
])
def test_session_capability_never_redirects_upload_to_a_different_target(monkeypatch, location):
    client, storage = install(monkeypatch)
    storage.hook = lambda request: storage.response(headers={} if location is None else {"location": location})
    with pytest.raises(GcsError):
        upload(client, storage)
    assert len(storage.requests) == 1 and all(s.closed for s in storage.streams)


@pytest.mark.parametrize("status,code", [(401, "GCS_ACCESS_DENIED"), (403, "GCS_ACCESS_DENIED"),
    (404, "GCS_OBJECT_ABSENT"), (412, "GCS_OBJECT_CONFLICT"), (302, "GCS_OUTCOME_UNCERTAIN"),
    (429, "GCS_OUTCOME_UNCERTAIN"), (500, "GCS_OUTCOME_UNCERTAIN"), (503, "GCS_OUTCOME_UNCERTAIN")])
def test_fixed_errors_without_body_read_redirect_or_automatic_retry(monkeypatch, status, code):
    client, storage = install(monkeypatch)
    storage.hook = lambda request: storage.response(status, body=b"SYNTHETIC-PRIVATE-ERROR" * 10000,
        headers={"location": "https://evil.example/"})
    with pytest.raises(GcsError, match=code) as caught:
        upload(client, storage)
    assert "PRIVATE" not in str(caught.value)
    assert len(storage.requests) == 1 and storage.streams[0].reads == 0 and storage.streams[0].closed


@pytest.mark.parametrize("ack", [None, "bytes=0-1", "bytes=1-8388607", "bytes=0-8388608", "bytes=0-8388607, bytes=0-1"])
def test_partial_or_ambiguous_chunk_acknowledgement_stops_without_replay(monkeypatch, ack):
    client, storage = install(monkeypatch, data=b"x" * (transport.UPLOAD_CHUNK_BYTES + 1))
    storage.hook = lambda r: storage.response(308, headers={} if ack is None else {"range": ack}) if r.method == "PUT" else None
    with pytest.raises(GcsError):
        upload(client, storage)
    assert [r.method for r in storage.requests] == ["POST", "PUT"]


@pytest.mark.parametrize("mutation", ["metadata", "encoding", "type", "size", "generation", "json-size", "duplicate-json", "readback-size", "readback-truncated", "readback-encoding", "readback-status"])
def test_unverified_remote_results_never_produce_a_receipt(monkeypatch, mutation):
    client, storage = install(monkeypatch)
    def hook(request):
        if request.method == "PUT" and not mutation.startswith("readback"):
            if mutation == "json-size":
                return storage.response(body=b"x" * (transport.JSON_BYTES + 1), headers={"content-type": "application/json"})
            if mutation == "duplicate-json":
                return storage.response(body=b'{"kind":"storage#object","kind":"storage#object"}', headers={"content-type": "application/json"})
            value = storage.metadata()
            key = {"metadata": "metadata", "encoding": "contentEncoding", "type": "contentType", "size": "size", "generation": "generation"}[mutation]
            value[key] = "SYNTHETIC-PRIVATE"
            return storage.response(value=value)
        if request.url.params.get("alt") == "media":
            return storage.response(206 if mutation == "readback-status" else 200,
                body=DATA[:-1] if mutation == "readback-truncated" else DATA,
                headers={"content-length": "1" if mutation == "readback-size" else str(len(DATA)),
                    "content-encoding": "gzip" if mutation == "readback-encoding" else "identity"})
    storage.hook = hook
    with pytest.raises(GcsError):
        upload(client, storage)
    assert all(s.closed for s in storage.streams)


@pytest.mark.parametrize("kind", ["deadline", "cancel", "busy"])
def test_interrupted_io_closes_response_and_releases_slot(monkeypatch, kind):
    client, storage = install(monkeypatch, timeout_seconds=.03 if kind == "deadline" else 2.0)
    storage.complete = True
    async def run():
        if kind == "deadline":
            storage.hook = lambda request: storage.response(value=storage.metadata(), wait=True)
            with pytest.raises(GcsError, match="GCS_OUTCOME_UNCERTAIN"):
                await client.reconcile(storage.spec)
        else:
            entered = anyio.Event()
            async def provider():
                entered.set()
                await anyio.sleep_forever()
            client._token_provider = provider
            async with anyio.create_task_group() as group:
                group.start_soon(client.reconcile, storage.spec)
                await entered.wait()
                if kind == "busy":
                    with pytest.raises(GcsError, match="GCS_BUSY"):
                        await client.reconcile(storage.spec)
                group.cancel_scope.cancel()
            client._token_provider = token
        storage.hook = None
        await client.reconcile(storage.spec)
    asyncio.run(run())
    assert all(s.closed for s in storage.streams)


def test_active_cancellation_during_response_read_finishes_async_cleanup(monkeypatch):
    client, storage = install(monkeypatch)
    storage.complete = True
    waiting = None
    class WaitingStream(Stream):
        async def __aiter__(self):
            waiting.set()
            await anyio.sleep_forever()
            yield b""
    def hook(request):
        stream = WaitingStream(b"")
        storage.streams.append(stream)
        return httpx.Response(200, headers={"content-type": "application/json"}, stream=stream)
    async def run():
        nonlocal waiting
        waiting = anyio.Event()
        storage.hook = hook
        async with anyio.create_task_group() as group:
            group.start_soon(client.reconcile, storage.spec)
            await waiting.wait()
            group.cancel_scope.cancel()
        storage.hook = None
        await client.reconcile(storage.spec)
    asyncio.run(run())
    assert all(s.closed for s in storage.streams)


def test_transport_diagnostics_never_log_session_token_or_remote_payload(monkeypatch, caplog):
    client, storage = install(monkeypatch)
    caplog.set_level(logging.DEBUG)
    def hook(request):
        for name in ("httpx", "httpcore.connection", "httpcore.http11"):
            logging.getLogger(name).debug("%s %s %s", TOKEN, SESSION_ID, "SYNTHETIC-PRIVATE")
    storage.hook = hook
    upload(client, storage)
    logging.getLogger("httpx").info("unrelated request remains visible")
    assert TOKEN not in caplog.text and SESSION_ID not in caplog.text and "SYNTHETIC-PRIVATE" not in caplog.text
    assert "unrelated request remains visible" in caplog.text


@pytest.mark.parametrize("changes", [{"size": 0}, {"size": True}, {"size": 16 * 1024**3 + 1},
    {"sha256": "bad"}, {"binding_sha256": "bad"}, {"job_id": "bad"}, {"relative_path": "../file"},
    {"relative_path": "/file"}, {"relative_path": "file\\other"}, {"relative_path": "file:other"},
    {"relative_path": "file\nother"}, {"relative_path": "x/" + "a" * 256}])
def test_invalid_or_forged_selection_rejected_before_network_or_source(monkeypatch, changes):
    client, storage = install(monkeypatch, selection=spec(**changes))
    with pytest.raises(GcsError, match="GCS_SELECTION_INVALID"):
        upload(client, storage)
    assert not storage.requests and not storage.eof


@pytest.mark.parametrize("credential", [None, SecretStr("short"), SecretStr("x" * 32 + "\n"),
    SecretStr("č" * 32), SecretStr("x" * 8193), "x" * 32])
def test_invalid_credentials_are_fixed_errors_and_never_open_connection(monkeypatch, credential):
    client, storage = install(monkeypatch)
    async def provider():
        return credential
    client._token_provider = provider
    with pytest.raises(GcsError, match="GCS_CREDENTIAL_UNAVAILABLE"):
        upload(client, storage)
    assert not storage.requests


def test_disabled_by_default_and_explicit_settings_credentials_redacted(monkeypatch):
    client, storage = install(monkeypatch)
    settings = Settings(_env_file=None)
    assert not settings.gcs_enabled
    with pytest.raises(GcsError, match="GCS_DISABLED"):
        upload(GcsClient.from_settings(settings), storage)
    configured = Settings(_env_file=None, gcs_enabled=True, gcs_bucket_name=BUCKET,
        gcs_staging_prefix="isolated/contracts", gcs_access_token=TOKEN)
    assert TOKEN not in repr(configured) and TOKEN not in configured.model_dump_json()
    assert upload(GcsClient.from_settings(configured), storage).spec == storage.spec


@pytest.mark.parametrize("changes", [{"gcs_bucket_name": ""}, {"gcs_bucket_name": "bucket/path"},
    {"gcs_bucket_name": "a"}, {"gcs_staging_prefix": ""}, {"gcs_staging_prefix": "../x"},
    {"gcs_staging_prefix": "/x"}, {"gcs_access_token": None}, {"gcs_access_token": "short"},
    {"gcs_timeout_seconds": 0}, {"gcs_timeout_seconds": 3601}])
def test_enabled_settings_fail_closed(changes):
    values = {"gcs_enabled": True, "gcs_bucket_name": BUCKET,
              "gcs_staging_prefix": "isolated/contracts", "gcs_access_token": TOKEN}
    values.update(changes)
    with pytest.raises(ValueError) as caught:
        Settings(_env_file=None, **values)
    assert TOKEN not in str(caught.value)
