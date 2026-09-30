from uuid import uuid4

import pytest

from app.core.config import Settings
from app.db.models import Project
from app.db.notion_sync_models import OrderFolderOperation
from app.main import create_app
from sqlalchemy import select
from test_application_access import access_case  # noqa: F401


def test_sync_configuration_is_visible_and_retry_has_no_io_when_disabled(access_case):
    with access_case.client("ADMIN") as client:
        customer = client.get("/api/customers").json()[0]
        path = "/api/notion-sync/CUSTOMER/" + customer["id"]
        assert client.get(path).json()["enabled"] is False
        assert client.post(path + "/retry").status_code == 503
    with access_case.client("PROCESSOR") as client:
        assert client.post(path + "/retry").status_code == 403


def test_folder_confirm_and_idempotency_bind_exact_payload(access_case, tmp_path, monkeypatch):
    async def idle_dispatcher(database, settings, stop): await stop.wait()
    monkeypatch.setattr("app.main.run_outbound_dispatcher", idle_dispatcher)
    settings = Settings(_env_file=None, cors_origins="https://testserver", auth_rate_limit_attempts=100,
        order_folders_enabled=True, order_folders_root=str(tmp_path))
    access_case.app = create_app(settings, access_case.database, access_case.worker)
    with access_case.database.session() as session:
        order = session.scalar(select(Project))
        order.project_number = "0253"
        order.name = "0253_ACME_SCANNING_092026"
        session.commit()
        order_id = str(order.id)
    with access_case.client("ADMIN") as client:
        order = client.get("/api/orders/" + order_id).json()
        path = "/api/orders/" + order_id + "/folder"
        key = str(uuid4())
        body = {"action": "CREATE", "expected_updated_at": order["updated_at"], "confirmed": True}
        assert client.post(path, json={**body, "confirmed": False}, headers={"Idempotency-Key": key}).status_code == 422
        response = client.post(path, json=body, headers={"Idempotency-Key": key})
        assert response.status_code == 202, response.text
        assert client.post(path, json=body, headers={"Idempotency-Key": key}).json() == response.json()
        assert client.post(path, json={**body, "expected_updated_at": "2020-01-01T00:00:00Z"}, headers={"Idempotency-Key": key}).status_code == 409
        assert client.post(path, json=body, headers={"Idempotency-Key": str(uuid4())}).status_code == 409
    with access_case.database.session() as session:
        assert len(list(session.scalars(select(OrderFolderOperation)))) == 1
    assert not list(tmp_path.iterdir())
