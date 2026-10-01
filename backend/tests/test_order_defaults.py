from datetime import UTC, datetime
from zoneinfo import ZoneInfo
import pytest
from test_application_access import access_case  # noqa: F401
from test_customer_orders import create_customer, create_order, write


def test_order_defaults_are_prague_today_and_do_not_reserve_or_consume_a_number(access_case, monkeypatch):
    import app.api.directory as directory
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 30, 22, 15, tzinfo=UTC).astimezone(tz)
    with access_case.client("ADMIN") as client:
        customer = create_customer(client)
        create_order(client, customer, number="0253")
        with monkeypatch.context() as context:
            context.setattr(directory, "datetime", Clock)
            expected = {"number": "0254", "starting_date": "2026-10-01"}
            assert client.get("/api/orders/defaults").json() == expected
            assert client.get("/api/orders/defaults").json() == expected
        created = create_order(client, customer)
        assert created["number"] == "0254"
        assert created["folder_operation"]["status"] == "PENDING"
        assert client.get("/api/orders/defaults").json()["number"] == "0255"
        duplicate = write(client, "post", "/api/orders", {"number": "0254", "customer_id": customer["id"], "project_type": "SCANNING", "starting_date": "2026-10-01"})
        assert duplicate.status_code == 409


@pytest.mark.parametrize("role,status", [(None, 401), ("ADMIN", 200), ("PRODUCTION_LEAD", 200), ("PROCESSOR", 403), ("LEADERSHIP", 403)])
def test_order_defaults_require_a_creator_role(access_case, role, status):
    with access_case.client(role) as client:
        response = client.get("/api/orders/defaults")
        assert response.status_code == status
        if status == 200:
            assert response.json()["starting_date"] == datetime.now(ZoneInfo("Europe/Prague")).date().isoformat()
