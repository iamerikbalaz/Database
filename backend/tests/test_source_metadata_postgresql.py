"""Metadata authorization races and receipt protection on owned PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.main import create_app
from test_application_access import ORIGIN, PASSWORD
from test_source_metadata_edit import MetadataStub, request_payload
from test_materials_postgresql import (POSTGRES_TEST_ADMIN_URL, isolated_postgresql_database,
    migrated_postgresql_url, review_pg_case)

pytestmark = [pytest.mark.skipif(POSTGRES_TEST_ADMIN_URL is None, reason="Requires isolated PostgreSQL test configuration"),
    pytest.mark.parametrize("review_pg_case", ["isolated-history"], indirect=True)]


def attach(case):
    worker = MetadataStub()
    app = create_app(case.app.state.settings, case.database, metadata_client=worker)
    def client_for(index=0):
        client = TestClient(app, base_url=ORIGIN)
        response = client.post("/api/auth/login", json={"email": case.users[index].email, "password": PASSWORD}, headers={"Origin": ORIGIN})
        assert response.status_code == 200
        client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]})
        return client
    return worker, client_for


@pytest.mark.parametrize("same_key", [True, False])
def test_metadata_competing_writes_own_one_operation_and_snapshot(review_pg_case, same_key):
    case = review_pg_case; worker, client_for = attach(case); barrier = Barrier(2)
    with client_for() as first, client_for(0 if same_key else 3) as second:
        body = request_payload(first, case.path)
        other = body if same_key else {**body, "idempotency_key": str(uuid4())}
        def send(client, payload):
            barrier.wait(timeout=10)
            return client.post(case.path + "/source-metadata", json=payload)
        with ThreadPoolExecutor(2) as pool:
            tasks = [pool.submit(send, first, body), pool.submit(send, second, other)]
            responses = [task.result(timeout=25) for task in tasks]
        assert sorted(item.status_code for item in responses) == ([200, 200] if same_key else [200, 409])
        if same_key: assert responses[0].json()["id"] == responses[1].json()["id"]
    assert len(worker.requests) == 1
    with case.database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM material_metadata_operations")) == 1
        assert connection.scalar(text("SELECT count(*) FROM pbr_material_metadata_snapshots")) == 1


def test_metadata_authorization_and_completed_receipt_are_immutable(review_pg_case):
    case = review_pg_case; worker, client_for = attach(case)
    worker.failed = True
    with client_for() as client:
        response = client.post(case.path + "/source-metadata", json=request_payload(client, case.path))
        assert response.status_code == 200 and response.json()["status"] == "RUNNING"
        for statement in ("UPDATE material_metadata_operations SET folder_path='changed'",
                          "UPDATE material_metadata_operations SET request_payload='{}'::jsonb"):
            with case.database.engine.begin() as connection, pytest.raises(DBAPIError): connection.execute(text(statement))
        worker.failed = False
        assert client.post(case.path + "/source-metadata/" + response.json()["id"] + "/resume").json()["status"] == "COMPLETED"
    for statement in ("UPDATE material_metadata_operations SET status='RUNNING'",
                      "UPDATE material_metadata_operations SET result='{}'::jsonb",
                      "DELETE FROM material_metadata_operations", "TRUNCATE material_metadata_operations"):
        with case.database.engine.begin() as connection, pytest.raises(DBAPIError): connection.execute(text(statement))
