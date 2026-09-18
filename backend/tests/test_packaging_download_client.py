"""Independent download verification against an actual synthetic runtime proof."""
import asyncio
import copy
import json

import anyio
import httpx
import pytest

from app import packaging_download_client as transport
from app.packaging_client import PackagingClientError
from app.packaging_contract import DispatchedPackagingResult
from test_packaging_client import client, fixture, prepared

DATA = b"Synthetic preview preserved"
PATH = "PREVIEW/preview.png"


class Stream(httpx.AsyncByteStream):
    def __init__(self, blocks, *, failure=None):
        self.blocks = blocks; self.failure = failure; self.closed = False; self.reads = 0
    async def __aiter__(self):
        for block in self.blocks:
            self.reads += 1
            yield block
        if self.failure == "timeout": await asyncio.sleep(1)
        elif self.failure: raise httpx.ReadError("PRIVATE")
    async def aclose(self):
        await anyio.sleep(0)
        self.closed = True


def source():
    value = fixture("packaging-dispatch-contract.json")
    return value, prepared(value), DispatchedPackagingResult.model_validate_json(json.dumps(value["result"]))


def install(monkeypatch, blocks=None, *, status=200, headers=None, failure=None):
    value, bound, result = source()
    item, selection = transport.download_selection(bound, value["report"], result, PATH)
    stream = Stream([DATA] if blocks is None else blocks, failure=failure)
    values = {"Content-Type":"application/octet-stream", "Content-Length":str(item.size),
        "X-Packaging-Proof-Sha256":selection["proof_sha256"], "X-Packaging-File-Sha256":item.sha256}
    if headers: values.update(headers)
    seen = []; original = httpx.AsyncClient
    def response(request):
        seen.append(request)
        assert request.method == "POST" and str(request.url) == "http://packaging:8081/internal/packaging/artifact"
        assert json.loads(request.content) == selection
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(status, headers=values, stream=stream)
    def connection(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        assert kwargs["timeout"].connect <= 5
        return original(**kwargs, transport=httpx.MockTransport(response))
    monkeypatch.setattr(httpx, "AsyncClient", connection)
    return value, bound, result, stream, seen


async def collect(parts):
    value, bound, result = parts[:3]
    async with client().open_artifact(bound, value["report"], result, PATH) as opened:
        chunks = [chunk async for chunk in opened.chunks()]
        with pytest.raises(PackagingClientError): _ = [chunk async for chunk in opened.chunks()]
        return chunks


def test_exact_proof_headers_bytes_and_bounded_single_use_transport(monkeypatch):
    parts = install(monkeypatch, [DATA[:4], DATA[4:15], DATA[15:]])
    monkeypatch.setattr(transport, "CHUNK_BYTES", 4)
    chunks = asyncio.run(collect(parts))
    assert b"".join(chunks) == DATA and max(map(len, chunks)) <= 4
    assert parts[3].closed and len(parts[4]) == 1


@pytest.mark.parametrize("headers", [{"Content-Type":"text/plain"}, {"Content-Encoding":"gzip"},
    {"Content-Length":"26, 26"}, {"Content-Length":"0"}, {"Content-Length":"-1"}, {"Content-Length":"PRIVATE"},
    {"X-Packaging-Proof-Sha256":"b"*64}, {"X-Packaging-File-Sha256":"b"*64}])
def test_header_substitution_is_rejected_without_reading_body(monkeypatch, headers):
    parts = install(monkeypatch, headers=headers)
    with pytest.raises(PackagingClientError): asyncio.run(collect(parts))
    assert parts[3].closed and parts[3].reads == 0 and len(parts[4]) == 1


@pytest.mark.parametrize("blocks", [[DATA[:-1]], [DATA + b"x"], [b"!" + DATA[1:]], []])
def test_truncation_overflow_corruption_never_emits_complete_expected_length(monkeypatch, blocks):
    parts = install(monkeypatch, blocks)
    monkeypatch.setattr(transport, "CHUNK_BYTES", 4)
    delivered = []
    async def run():
        async with client().open_artifact(parts[1], parts[0]["report"], parts[2], PATH) as opened:
            async for chunk in opened.chunks(): delivered.append(chunk)
    with pytest.raises(PackagingClientError): asyncio.run(run())
    assert sum(map(len, delivered)) < len(DATA) and parts[3].closed and len(parts[4]) == 1


@pytest.mark.parametrize("kind", ["network", "timeout", "consumer", "cancel"])
def test_every_interrupted_lifecycle_closes_upstream_without_retry(monkeypatch, kind):
    parts = install(monkeypatch, [DATA[:8]], failure=kind if kind in {"network", "timeout"} else None)
    monkeypatch.setattr(transport, "CHUNK_BYTES", 4)
    if kind == "timeout": monkeypatch.setattr(transport, "TRANSFER_SECONDS", .02)
    class ConsumerFailure(Exception): pass
    async def run():
        async with client().open_artifact(parts[1], parts[0]["report"], parts[2], PATH) as opened:
            async for _ in opened.chunks():
                if kind == "consumer": raise ConsumerFailure()
                if kind == "cancel": raise asyncio.CancelledError()
    expected = ConsumerFailure if kind == "consumer" else asyncio.CancelledError if kind == "cancel" else PackagingClientError
    with pytest.raises(expected) as error: asyncio.run(run())
    assert "PRIVATE" not in str(error.value) and parts[3].closed and len(parts[4]) == 1


@pytest.mark.parametrize("status,content,code", [(503, {"detail":{"code":"PACKAGING_SERVICE_BUSY"}}, "PACKAGING_SERVICE_BUSY"),
    (409, {"detail":{"code":"PRIVATE"}}, "PACKAGING_UNAVAILABLE"), (302, {}, "PACKAGING_DOWNLOAD_UNAVAILABLE"),
    (404, {"detail":{"code":"PACKAGING_STORE_FILE_NOT_FOUND"}}, "PACKAGING_STORE_FILE_NOT_FOUND")])
def test_fixed_error_codes_and_no_redirect_following(monkeypatch, status, content, code):
    parts = install(monkeypatch, [json.dumps(content).encode()], status=status, headers={"Content-Type":"application/json"})
    with pytest.raises(PackagingClientError) as error: asyncio.run(collect(parts))
    assert error.value.code == code and "PRIVATE" not in str(error.value)
    assert parts[3].closed and len(parts[4]) == 1


def test_error_body_size_bound(monkeypatch):
    parts = install(monkeypatch, [b"x" * 5000], status=503, headers={"Content-Type":"application/json"})
    with pytest.raises(PackagingClientError): asyncio.run(collect(parts))
    assert parts[3].closed


def test_active_cancellation_scope_still_finishes_async_upstream_close(monkeypatch):
    parts = install(monkeypatch)
    async def run():
        with anyio.move_on_after(.02) as cancel:
            async with client().open_artifact(parts[1], parts[0]["report"], parts[2], PATH):
                await anyio.sleep(1)
        assert cancel.cancel_called
    asyncio.run(run())
    assert parts[3].closed and len(parts[4]) == 1


@pytest.mark.parametrize("change", ["path", "proof", "request", "report", "mutated-model", "disabled"])
def test_no_network_before_independent_proof_and_configuration_checks(monkeypatch, change):
    value, bound, result = source(); path = PATH; service = client(enabled=change != "disabled")
    if change == "path": path = "../PRIVATE"
    elif change == "proof": object.__setattr__(result.stored, "proof_sha256", "b" * 64)
    elif change == "request": object.__setattr__(bound, "request_hash", "b" * 64)
    elif change == "report": value["report"] = {}
    elif change == "mutated-model": result.stored.payload.files.append(copy.deepcopy(result.stored.payload.files[0]))
    def forbidden(**kwargs): pytest.fail("Invalid selection must not reach HTTP")
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    async def run():
        async with service.open_artifact(bound, value["report"], result, path): pytest.fail("Unexpected download")
    with pytest.raises(PackagingClientError): asyncio.run(run())
