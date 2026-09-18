"""Synthetic database failures through the real application middleware stack."""
import asyncio
import logging
import traceback
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.config import Settings
from app.db.errors import DatabaseErrorBoundary, DatabaseResponseInterrupted
from app.db.session import Database
from app.main import create_app


ORIGIN = "https://localhost"


@pytest.fixture
def error_case():
    database = Database("sqlite+pysqlite:///:memory:")
    with database.engine.begin() as connection:
        connection.execute(text("CREATE TABLE error_probe (value TEXT UNIQUE)"))
    app = create_app(Settings(_env_file=None, cors_origins=ORIGIN), database)
    yield app, database
    database.dispose()


def assert_safe(caplog, response, marker):
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "DATABASE_UNAVAILABLE"}}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert marker not in response.text and marker not in caplog.text
    records = [item for item in caplog.records if item.name == "reawote.database"]
    assert len(records) == 1
    assert records[0].getMessage() == "database_request_failed"
    assert records[0].exc_info is None and not records[0].response_started


def test_real_driver_hides_bound_parameters_and_http_error(error_case, caplog):
    app, database = error_case
    marker = uuid4().hex
    with database.engine.begin() as connection:
        connection.execute(text("INSERT INTO error_probe VALUES (:value)"), {"value": marker})
    with pytest.raises(IntegrityError) as captured:
        with database.engine.begin() as connection:
            connection.execute(text("INSERT INTO error_probe VALUES (:value)"), {"value": marker})
    assert captured.value.hide_parameters
    assert marker not in str(captured.value)

    @app.post("/api/error-probe")
    def fail():
        with database.engine.begin() as connection:
            connection.execute(text("INSERT INTO error_probe VALUES (:value)"), {"value": marker})

    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post("/api/error-probe", headers={"Origin": ORIGIN})
    assert_safe(caplog, response, marker)


@pytest.mark.parametrize("grouped", [False, True])
def test_driver_detail_and_statement_are_never_formatted(error_case, caplog, grouped):
    app, _ = error_case
    marker = uuid4().hex

    @app.get("/api/error-probe")
    def fail():
        failure = OperationalError(marker, {"value": marker}, RuntimeError(marker))
        if grouped:
            raise ExceptionGroup(marker, [ExceptionGroup(marker, [failure]), ValueError(marker)])
        raise failure

    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get("/api/error-probe", headers={"Origin": ORIGIN})
    assert_safe(caplog, response, marker)


def test_error_after_commit_does_not_retry_or_claim_the_write_was_rolled_back(error_case, caplog):
    app, database = error_case
    marker = uuid4().hex
    attempts = []

    @app.post("/api/error-probe")
    def commit_then_fail():
        attempts.append(True)
        with database.engine.begin() as connection:
            connection.execute(text("INSERT INTO error_probe VALUES (:value)"), {"value": marker})
        raise OperationalError(marker, (), RuntimeError(marker))

    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post("/api/error-probe", headers={"Origin": ORIGIN})
        with database.engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM error_probe")) == 1
    assert len(attempts) == 1
    assert_safe(caplog, response, marker)


@pytest.mark.parametrize("status", [401, 403, 409, 422])
def test_handled_domain_errors_keep_their_contract(error_case, caplog, status):
    app, _ = error_case

    @app.post("/api/error-probe")
    def reject():
        raise HTTPException(status, {"code": "EXPLICIT_DOMAIN_REJECTION"})

    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post("/api/error-probe", headers={"Origin": ORIGIN})
    assert response.status_code == status
    assert response.json() == {"detail": {"code": "EXPLICIT_DOMAIN_REJECTION"}}
    assert response.headers["cache-control"] == "no-store"
    assert not [item for item in caplog.records if item.name == "reawote.database"]


def test_streaming_database_failure_terminates_without_original_diagnostics(error_case, caplog):
    app, _ = error_case
    marker = uuid4().hex

    @app.get("/api/error-probe")
    def stream():
        async def chunks():
            yield b"accepted prefix"
            raise OperationalError(marker, {"value": marker}, RuntimeError(marker))
        return StreamingResponse(chunks())

    with TestClient(app, base_url=ORIGIN) as client:
        with pytest.raises(DatabaseResponseInterrupted) as captured:
            client.get("/api/error-probe")
    formatted = "".join(traceback.format_exception(captured.value))
    assert marker not in formatted and marker not in caplog.text
    assert captured.value.__suppress_context__
    records = [item for item in caplog.records if item.name == "reawote.database"]
    assert len(records) == 1 and records[0].response_started and records[0].exc_info is None


@pytest.mark.parametrize("grouped", [False, True])
def test_unrelated_exceptions_are_not_disguised_as_database_failures(error_case, caplog, grouped):
    app, _ = error_case

    @app.get("/api/error-probe")
    def fail():
        if grouped:
            raise ExceptionGroup("Unrelated failure", [ValueError("Unrelated failure"), TypeError("Unrelated failure")])
        raise ValueError("Unrelated failure")

    with TestClient(app) as client:
        with pytest.raises(ExceptionGroup if grouped else ValueError):
            client.get("/api/error-probe")
    assert not [item for item in caplog.records if item.name == "reawote.database"]


def test_boundary_does_not_emit_a_second_response_after_headers(caplog):
    marker = uuid4().hex
    sent = []

    async def upstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"partial", "more_body": True})
        raise OperationalError(marker, (), RuntimeError(marker))

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request"}

    with caplog.at_level(logging.ERROR), pytest.raises(DatabaseResponseInterrupted):
        asyncio.run(DatabaseErrorBoundary(upstream)({"type": "http"}, receive, send))
    assert [item["type"] for item in sent] == ["http.response.start", "http.response.body"]
    assert sent[-1]["more_body"] is True
    assert marker not in caplog.text
