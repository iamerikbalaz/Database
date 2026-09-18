"""Synthetic contracts only: no Notion account, credentials or external IO."""
import asyncio
from copy import deepcopy
import json
import logging
from uuid import uuid4

import anyio
import httpx
from pydantic import SecretStr
import pytest

from app import notion_reader as reader
from app.notion_reader import NotionConfiguration, NotionError, NotionReader, Property

SOURCE = str(uuid4()); DATABASE = str(uuid4()); PAGE = str(uuid4())
TOKEN = "synthetic-notion-" + "x" * 40


def config(**changes):
    return NotionConfiguration(**(dict(enabled=True, data_source_id=SOURCE, properties=(
        Property(field="name", property_id="title"), Property(field="website", property_id="url%3A"),
        Property(field="country", property_id="country"))) | changes))


def text(value): return {"type": "text", "text": {"content": value, "link": None}, "plain_text": value}


def schema():
    return {"object": "data_source", "id": SOURCE, "in_trash": False, "parent": {"type": "database_id", "database_id": DATABASE},
        "properties": {"Renamable name": {"id": "title", "type": "title", "title": {}},
            "Renamable website": {"id": "url%3A", "type": "url", "url": {}},
            "Country": {"id": "country", "type": "rich_text", "rich_text": {}}}}


def page():
    return {"object": "page", "id": PAGE, "in_trash": False,
        "parent": {"type": "data_source_id", "data_source_id": SOURCE, "database_id": DATABASE},
        "last_edited_time": "2026-09-18T12:00:00.000Z", "properties": {
            "A new display name": {"id": "title", "type": "title", "title": [text("Synthetic česká company")]},
            "Website": {"id": "url%3A", "type": "url", "url": "https://example.invalid"},
            "Country": {"id": "country", "type": "rich_text", "rich_text": []},
            "Private unmapped": {"id": "hidden", "type": "rich_text", "rich_text": [text("SYNTHETIC-PRIVATE-CONTENT")]}}}


class Stream(httpx.AsyncByteStream):
    def __init__(self, data, delay=0): self.data = data; self.delay = delay; self.closed = False; self.reads = 0
    async def __aiter__(self):
        if self.delay: await anyio.sleep(self.delay)
        for offset in range(0, len(self.data), 4096):
            self.reads += 1; yield self.data[offset:offset + 4096]
    async def aclose(self):
        await anyio.sleep(0); self.closed = True


class Server:
    def __init__(self): self.requests = []; self.streams = []; self.hook = None
    def response(self, value=None, *, data=None, status=200, headers=None, delay=0):
        stream = Stream(data if data is not None else json.dumps(value).encode(), delay); self.streams.append(stream)
        return httpx.Response(status, headers={"content-type": "application/json", **(headers or {})}, stream=stream)
    async def handle(self, request):
        self.requests.append(request)
        if self.hook: return await self.hook(request)
        return self.response(schema() if "/data_sources/" in str(request.url) else page(), headers={"set-cookie": "synthetic=must-not-be-reused; Path=/"})
    def client(self, configuration=None, token=None):
        return NotionReader(configuration or config(), token if token is not None else SecretStr(TOKEN), transport=httpx.MockTransport(self.handle))


def read(client, identifier=PAGE, **kwargs): return asyncio.run(client.company(identifier, **kwargs))


def test_exact_target_version_explicit_ids_and_only_mapped_values(monkeypatch):
    server = Server(); monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    result = read(server.client(), PAGE.replace("-", "").upper())
    assert [str(request.url) for request in server.requests] == [reader.ORIGIN + "/v1/data_sources/" + SOURCE, reader.ORIGIN + "/v1/pages/" + PAGE]
    assert all(request.method == "GET" and not request.content for request in server.requests)
    assert all(request.headers["Notion-Version"] == reader.VERSION and request.headers["Authorization"] == "Bearer " + TOKEN
        and "cookie" not in request.headers for request in server.requests)
    assert {value.field: value.value for value in result.values} == {"name": "Synthetic česká company", "website": "https://example.invalid/", "country": None}
    assert result.page_id == PAGE and result.database_id == DATABASE and len(result.observation_sha256) == 64
    assert "SYNTHETIC-PRIVATE-CONTENT" not in result.model_dump_json() and "Synthetic česká company" not in repr(result)
    assert all(stream.closed for stream in server.streams)


def test_projection_digest_changes_for_context_or_mapped_values_but_not_unmapped_data():
    original = reader.project(config(), PAGE, schema(), page()); document = page()
    document["properties"]["Private unmapped"]["rich_text"] = [text("Another private value")]
    assert reader.project(config(), PAGE, schema(), document) == original
    document["properties"]["A new display name"]["title"] = [text("Changed name")]
    assert reader.project(config(), PAGE, schema(), document).observation_sha256 != original.observation_sha256
    document = page(); document["last_edited_time"] = "2026-09-18T13:00:00Z"
    assert reader.project(config(), PAGE, schema(), document).observation_sha256 != original.observation_sha256


@pytest.mark.parametrize("identifier", ["../elsewhere", "https://example.invalid", PAGE + "?x=y", "{" + PAGE + "}", "0" * 32, None, True])
def test_invalid_identifiers_never_send_http(identifier):
    server = Server()
    with pytest.raises(NotionError, match="NOTION_SELECTION_INVALID"): read(server.client(), identifier)
    assert not server.requests


@pytest.mark.parametrize("value", ["", "short", "x" * 31, "x" * 8193, "x" * 35 + "\n", "č" * 40],
    ids=["empty", "short", "under-limit", "oversize", "newline", "unicode"])
def test_invalid_credentials_fail_without_io(value):
    server = Server()
    with pytest.raises(NotionError, match="NOTION_CONFIGURATION_INVALID"): read(server.client(token=SecretStr(value)))
    assert not server.requests


def test_disabled_and_forged_configuration_never_send_http():
    server = Server()
    with pytest.raises(NotionError, match="NOTION_DISABLED"): read(server.client(NotionConfiguration()))
    with pytest.raises(NotionError, match="NOTION_CONFIGURATION_INVALID"):
        read(server.client(config().model_copy(update={"enabled": "true"})))
    assert not server.requests


@pytest.mark.parametrize("changes", [dict(properties=()), dict(data_source_id="https://example.invalid"),
    dict(properties=(Property(field="name", property_id="title"), Property(field="country", property_id="title"))),
    dict(properties=(Property(field="name", property_id="title"), Property(field="name", property_id="another")))])
def test_mapping_is_explicit_unique_and_requires_a_name(changes):
    with pytest.raises((ValueError, NotionError)): config(**changes)


@pytest.mark.parametrize("change", ["trashed", "wrong_id", "wrong_source", "wrong_database", "no_parent", "bad_time", "duplicate_id", "missing_property", "wrong_type", "mentions", "has_more", "too_long", "control", "invalid_url"])
def test_rejects_unrelated_incomplete_or_unusable_page(change):
    value = page(); props = value["properties"]
    if change == "trashed": value["in_trash"] = True
    elif change == "wrong_id": value["id"] = SOURCE
    elif change == "wrong_source": value["parent"]["data_source_id"] = PAGE
    elif change == "wrong_database": value["parent"]["database_id"] = PAGE
    elif change == "no_parent": value.pop("parent")
    elif change == "bad_time": value["last_edited_time"] = "2026-09-18T12:00:00"
    elif change == "duplicate_id": props["Duplicate"] = deepcopy(props["Country"])
    elif change == "missing_property": props.pop("Country")
    elif change == "wrong_type": props["Country"]["type"] = "formula"
    elif change == "mentions": props["A new display name"]["title"] = [{"type": "mention", "plain_text": "Unsupported person"}]
    elif change == "has_more": props["Country"]["has_more"] = True
    elif change == "too_long": props["A new display name"]["title"] = [text("x" * 256)]
    elif change == "control": props["A new display name"]["title"] = [text("Bad\x00text")]
    elif change == "invalid_url": props["Website"]["url"] = "javascript:alert(1)"
    with pytest.raises(NotionError): reader.project(config(), PAGE, schema(), value)


@pytest.mark.parametrize("status,code", [(301, "NOTION_UNAVAILABLE"), (401, "NOTION_ACCESS_DENIED"), (403, "NOTION_ACCESS_DENIED"),
    (404, "NOTION_PAGE_UNAVAILABLE"), (409, "NOTION_UNAVAILABLE"), (500, "NOTION_UNAVAILABLE"), (503, "NOTION_UNAVAILABLE")])
def test_status_errors_are_bounded_do_not_retry_or_parse_remote_errors(status, code):
    server = Server()
    async def fail(_): return server.response(data=b"SYNTHETIC-PRIVATE-ERROR", status=status, headers={"location": "https://example.invalid/"})
    server.hook = fail
    with pytest.raises(NotionError, match=code) as error: read(server.client())
    assert str(error.value) == code and len(server.requests) == 1
    assert server.streams[0].closed and server.streams[0].reads == 0


@pytest.mark.parametrize("status", [429, 529])
def test_retry_after_is_honored_without_automatic_replay(monkeypatch, status):
    server = Server(); client = server.client()
    async def limited(_): return server.response({}, status=status, headers={"Retry-After": "120"})
    server.hook = limited
    with pytest.raises(NotionError) as first: read(client)
    assert first.value.code == "NOTION_RATE_LIMITED" and first.value.retry_after == 120
    with pytest.raises(NotionError) as second: read(client)
    assert 1 <= second.value.retry_after <= 120 and len(server.requests) == 1
    server.hook = None; client._retry_at = 0
    assert read(client).page_id == PAGE


@pytest.mark.parametrize("body,headers", [(b'{"id":1,"id":2}', {}), (b'{"x":NaN}', {}), (b'[]', {}),
    (b'{}', {"Content-Encoding": "gzip"}), (b'{}', {"Content-Type": "text/html"}),
    (b'{}', {"Content-Length": str(reader.MAX_RESPONSE_BYTES + 1)}),
    (b'x' * (reader.MAX_RESPONSE_BYTES + 4096), {})],
    ids=["duplicate", "nan", "array", "compressed", "html", "oversize-header", "oversize-stream"])
def test_bounded_strict_json_closes_stream_on_invalid_response(body, headers):
    server = Server()
    async def invalid(_): return server.response(data=body, headers=headers)
    server.hook = invalid
    with pytest.raises(NotionError, match="NOTION_RESPONSE_INVALID"): read(server.client())
    assert len(server.requests) == 1 and server.streams[0].closed


def test_deadline_releases_slot_and_closes_stream():
    server = Server(); client = server.client(config(timeout_seconds=0.01))
    async def slow(_): return server.response(schema(), delay=1)
    server.hook = slow
    with pytest.raises(NotionError, match="NOTION_UNAVAILABLE"): read(client)
    assert server.streams[0].closed
    server.hook = None; client.configuration = config()
    assert read(client).page_id == PAGE


def test_cancellation_and_simultaneous_operation_release_owned_resources():
    async def scenario():
        server = Server(); client = server.client(); entered = anyio.Event()
        async def slow(_): entered.set(); return server.response(schema(), delay=20)
        server.hook = slow
        async with anyio.create_task_group() as group:
            group.start_soon(client.company, PAGE); await entered.wait()
            with pytest.raises(NotionError, match="NOTION_BUSY"): await client.company(PAGE)
            await anyio.sleep(0); group.cancel_scope.cancel()
        assert all(stream.closed for stream in server.streams)
        server.hook = None; assert (await client.company(PAGE)).page_id == PAGE
    asyncio.run(scenario())


def test_operation_guard_is_rechecked_after_io_and_never_leaks_details():
    server = Server(); checks = 0
    async def guard():
        nonlocal checks
        checks += 1
        if checks == 2: raise RuntimeError("SYNTHETIC-PRIVATE-AUTH-ERROR")
    with pytest.raises(NotionError, match="NOTION_OPERATION_BLOCKED"): read(server.client(), operation_guard=guard)
    assert len(server.requests) == 1 and server.streams[0].closed


def test_verbose_transport_diagnostics_and_exception_payloads_stay_private(caplog):
    server = Server()
    async def fail(_):
        logging.getLogger("httpcore.http11").debug("SYNTHETIC-PRIVATE-HEADER " + TOKEN)
        raise httpx.ReadError("SYNTHETIC-PRIVATE-REMOTE-ERROR")
    server.hook = fail
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(NotionError, match="NOTION_UNAVAILABLE") as error: read(server.client())
        logging.getLogger("httpcore.http11").debug("PUBLIC-AFTER-OPERATION")
    assert "SYNTHETIC-PRIVATE" not in caplog.text and TOKEN not in caplog.text
    assert "PUBLIC-AFTER-OPERATION" in caplog.text and str(error.value) == "NOTION_UNAVAILABLE"
