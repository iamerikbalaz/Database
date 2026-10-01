import csv
import io
from uuid import UUID

from sqlalchemy import select
from app.db.models import PublishedBrand
from app.db.notion_sync_models import NotionSyncState
from app.notion_outbound import notion_properties
from test_application_access import access_case  # noqa: F401
from test_customer_orders import create_customer, write


def test_brand_csv_preserves_template_and_warns_without_publishing(access_case):
    with access_case.client("ADMIN") as client:
        row = create_customer(client, description='A; "quoted"\ntext', country="Česko", website="https://example.invalid")
        response = client.post("/api/customer-exports/csv", json={"customer_ids": [row["id"]]})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["filename"] == "CSV_BRANDS.csv" and body["csv"].startswith("\ufeff")
        parsed = list(csv.reader(io.StringIO(body["csv"].lstrip("\ufeff")), delimiter=";"))
        assert parsed == [["brand_identifier", "name", "description", "website", "country"], ["E001", "English-Dekor", 'A; "quoted"\ntext', "https://example.invalid/", "Česko"]]
        assert body["warnings"] == []
        assert client.get("/api/customers/" + row["id"]).json()["is_published"] is False


def test_published_edit_filters_history_and_outbox(access_case):
    with access_case.client("ADMIN") as client:
        row = create_customer(client)
        changed = write(client, "patch", "/api/customers/" + row["id"], {"expected_updated_at": row["updated_at"], "is_published": True, "status": "Test sample"})
        assert changed.status_code == 200, changed.text
        assert changed.json()["is_published"] is True
        assert row["id"] in [x["id"] for x in client.get("/api/customers?is_published=true").json()]
        assert row["id"] not in [x["id"] for x in client.get("/api/customers?is_published=false").json()]
        history = client.get("/api/customers/" + row["id"] + "/history").json()["items"]
        assert any(event["action"] == "UPDATED" and event["after"]["is_published"] is True for event in history)
    with access_case.database.session() as session:
        state = session.scalar(select(NotionSyncState).where(NotionSyncState.entity_id == UUID(row["id"])))
        assert notion_properties("CUSTOMER", state.payload)["Published"] == {"checkbox": True}
        state.payload = {**state.payload, "is_published": False}
        assert notion_properties("CUSTOMER", state.payload)["Published"] == {"checkbox": False}


def test_brand_export_rejects_invalid_identifiers_and_formulas(access_case):
    with access_case.client("ADMIN") as client:
        row = create_customer(client, brand_identifier="A001, B001")
        assert client.post("/api/customer-exports/csv", json={"customer_ids": [row["id"]]}).status_code == 422
        second = create_customer(client, name="Formula brand", brand_identifier="SECOND", notes="safe", description="=HYPERLINK(1)")
        assert client.post("/api/customer-exports/csv", json={"customer_ids": [second["id"]]}).status_code == 422
        assert client.post("/api/customer-exports/csv", json={"customer_ids": [second["id"], second["id"]]}).status_code == 422
    with access_case.client("PROCESSOR") as client:
        assert client.post("/api/customer-exports/csv", json={"customer_ids": [row["id"]]}).status_code == 403


def test_old_outbox_does_not_clear_published_and_uses_renamed_status():
    properties = notion_properties("CUSTOMER", {"customer_status": "Active"})
    assert "Published" not in properties
    assert properties["Status"] == {"status": {"name": "Active cooperation"}}
