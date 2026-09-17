"""Real auth sessions and bounded import transport, without production source IO."""
import asyncio
import json

from fastapi import HTTPException
import pytest
from sqlalchemy import func, select
from starlette.requests import Request

from app.api import material_imports
from app.db.models import InternalUser, PBRMaterial, UserCredential
from app.import_requests import InspectImport
from app.import_transport import ImportRequestReader, decode_request
from test_application_access import access_case
from test_import_requests import COLUMNS, csv_payload

PATH = "/api/material-imports/inspect"


def payload():
    return {"source": csv_payload().model_dump(), "columns": COLUMNS}


@pytest.mark.parametrize("role,status", [(None, 401), ("PROCESSOR", 403), ("OTHER", 403),
    ("LEADERSHIP", 403), ("PRODUCTION_LEAD", 403), ("ADMIN", 200)])
def test_import_inspection_uses_real_sessions_and_admin_only(access_case, role, status, monkeypatch):
    calls = []
    original = material_imports.inspect_source
    def inspect(value):
        calls.append(True)
        return original(value)
    monkeypatch.setattr(material_imports, "inspect_source", inspect)
    with access_case.client(role) as client:
        result = client.post(PATH, json=payload())
        assert result.status_code == status
        assert result.headers["cache-control"] == "no-store"
        if status == 200:
            assert result.json()["row_count"] == 1
            assert result.json()["sample"][0]["row"] == 2
        else:
            assert not calls
    with access_case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(PBRMaterial)) == 2


@pytest.mark.parametrize("change,status", [("disable", 401), ("demote", 403), ("password", 403)])
@pytest.mark.parametrize("invalid_source", [False, True])
def test_inspection_reauthorizes_before_disclosing_samples_or_errors(access_case, monkeypatch, change, status, invalid_source):
    original = material_imports.inspect_source
    def during_parse(value):
        with access_case.database.session() as session:
            user = session.get(InternalUser, access_case.users["ADMIN"].id)
            if change == "disable": user.is_active = False
            elif change == "demote": user.role = "LEADERSHIP"
            else: session.get(UserCredential, user.id).must_change_password = True
            session.commit()
        return original(value)
    monkeypatch.setattr(material_imports, "inspect_source", during_parse)
    body = payload()
    if invalid_source: body["source"]["data"] = "PRIVATE_SYNTHETIC_MARKER"
    with access_case.client("ADMIN") as client:
        result = client.post(PATH, json=body)
        assert result.status_code == status
        assert "sample" not in result.text and "IMPORT_BASE64" not in result.text


def test_invalid_payloads_never_reflect_uploads_or_user_controlled_keys(access_case, caplog):
    marker = "PRIVATE_SYNTHETIC_MARKER"
    bodies = [{marker: marker}, {**payload(), "extra": marker},
              {**payload(), "source": {"format": "CSV", "delimiter": ";", "data": marker}},
              {**payload(), "columns": {**COLUMNS, "name": marker}}]
    with access_case.client("ADMIN") as client:
        for body in bodies:
            result = client.post(PATH, json=body)
            assert result.status_code == 422 and marker not in result.text
        malformed = client.post(PATH, content='{"' + marker, headers={"Content-Type": "application/json"})
        assert malformed.status_code == 422 and marker not in malformed.text
    assert marker not in caplog.text


def test_inspection_requires_csrf_origin_and_completed_password_change(access_case):
    with access_case.client("ADMIN") as client:
        client.headers.pop("X-CSRF-Token")
        assert client.post(PATH, json=payload()).status_code == 403
    with access_case.client("ADMIN") as client:
        assert client.post(PATH, json=payload(), headers={"Origin": "https://untrusted.invalid"}).status_code == 403
        with access_case.database.session() as session:
            session.get(UserCredential, access_case.users["ADMIN"].id).must_change_password = True
            session.commit()
        assert client.post(PATH, json=payload()).status_code == 403


@pytest.mark.parametrize("raw", [b'{"source":{},"source":{}}', b'{"x":NaN}', b'{"x":Infinity}',
    b'{"x":-Infinity}', b'{"x":{"nested":1,"nested":2}}', b'{"x":"\xff"}', b'[' * 2000])
def test_json_syntax_is_strict_and_errors_are_static(raw):
    with pytest.raises(HTTPException) as caught:
        decode_request(raw, InspectImport)
    assert caught.value.status_code == 422
    assert caught.value.detail == {"code": "IMPORT_REQUEST_INVALID"}


def request(chunks, headers=None, *, delay=0, disconnect=False):
    chunks = iter(chunks)
    async def receive():
        if delay: await asyncio.sleep(delay)
        if disconnect: return {"type": "http.disconnect"}
        part = next(chunks, None)
        return {"type": "http.request", "body": part or b"", "more_body": part is not None}
    return Request({"type": "http", "method": "POST", "path": PATH,
                    "headers": [(key.encode(), value.encode()) for key, value in
                                ({"content-type": "application/json", **(headers or {})}).items()]}, receive)


@pytest.mark.parametrize("headers,chunks,code,status", [
    ({"content-length": "7"}, [b"1234567"], "IMPORT_REQUEST_SIZE", 413),
    ({}, [b"123", b"4567"], "IMPORT_REQUEST_SIZE", 413),
    ({"content-length": "5"}, [b"123"], "IMPORT_REQUEST_SIZE", 413),
    ({"content-length": "-1"}, [b"1"], "IMPORT_REQUEST_SIZE", 413),
    ({"content-length": "0000000000000001"}, [b"1"], "IMPORT_REQUEST_SIZE", 413),
    ({}, [], "IMPORT_REQUEST_SIZE", 413),
    ({"content-type": "text/plain"}, [b"1"], "IMPORT_JSON_REQUIRED", 415),
    ({"content-encoding": "gzip"}, [b"1"], "IMPORT_CONTENT_ENCODING", 415),
])
def test_stream_limit_checks_actual_bytes_headers_and_releases_slots(monkeypatch, headers, chunks, code, status):
    monkeypatch.setattr("app.import_transport.MAX_REQUEST_BYTES", 6)
    reader = ImportRequestReader()
    async def run():
        with pytest.raises(HTTPException) as caught:
            async with reader.body(request(chunks, headers)):
                pytest.fail("Invalid body accepted")
        assert caught.value.status_code == status and caught.value.detail == {"code": code}
        async with reader.body(request([b"123", b"456"], {"content-length": "6"})) as raw:
            assert raw == b"123456"
    asyncio.run(run())


def test_slots_bound_simultaneous_imports_through_the_complete_operation():
    reader = ImportRequestReader()
    async def run():
        async with reader.body(request([b"1"])):
            async with reader.body(request([b"2"])):
                with pytest.raises(HTTPException) as caught:
                    async with reader.body(request([b"3"])): pytest.fail("Third slot accepted")
                assert caught.value.status_code == 503
            async with reader.body(request([b"4"])) as raw: assert raw == b"4"
    asyncio.run(run())


@pytest.mark.parametrize("disconnected", [False, True])
def test_timeout_and_disconnect_release_upload_slot(monkeypatch, disconnected):
    monkeypatch.setattr("app.import_transport.UPLOAD_TIMEOUT_SECONDS", 0.01)
    reader = ImportRequestReader()
    async def run():
        with pytest.raises(HTTPException) as caught:
            async with reader.body(request([], disconnect=disconnected, delay=0 if disconnected else 1)):
                pytest.fail("Interrupted upload accepted")
        assert caught.value.status_code == (400 if disconnected else 408)
        async with reader.body(request([b"ok"])) as raw: assert raw == b"ok"
    asyncio.run(run())
