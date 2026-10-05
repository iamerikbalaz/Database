"""Historical spreadsheet properties keep units, provenance and exact retries."""
import base64
from decimal import Decimal
from uuid import UUID
import pytest
from sqlalchemy import func, select
from app.db.models import MaterialApproval, PBRMaterial, PublishedBrand, ResourceChangeEvent
from app.db.directory_models import DirectoryChangeEvent
from app.db.notion_sync_models import NotionSyncState
from app.import_properties import parse_properties
from test_application_access import access_case
from test_import_confirm import confirmation
from test_import_preview import plan_payload


@pytest.mark.parametrize("size,expected", [("10x10-cm", ("10", "10")), ("10x20", ("10", "20")), ("1,25 × 2.5 cm", ("1.25", "2.5")), ("", (None, None))])
def test_centimetres_are_explicit_or_the_column_default(size, expected):
    values, findings = parse_properties({"sample_size": size})
    assert findings == [] and (values["width_cm"], values["height_cm"]) == expected


@pytest.mark.parametrize("field,value,code", [
    ("sample_size", "100x100-m", "IMPORT_SAMPLE_SIZE_INVALID"), ("sample_size", "0x10", "IMPORT_SAMPLE_SIZE_INVALID"),
    ("sample_size", "1.00001x2", "IMPORT_SAMPLE_SIZE_INVALID"), ("sample_size", "100000000x2", "IMPORT_SAMPLE_SIZE_INVALID"),
    ("color", "#NOTHEX", "IMPORT_COLOR_INVALID"), ("done", "Needs rerender", "IMPORT_DONE_INVALID"),
    ("checked", "YES", "IMPORT_CHECKED_REQUIRES_DONE"), ("brand_identifier", "x" * 256, "IMPORT_BRAND_IDENTIFIER_INVALID")])
def test_ambiguous_units_or_statuses_are_not_guessed(field, value, code):
    _, findings = parse_properties({field: value})
    assert (field, code) in findings


def property_body(case, **overrides):
    body = plan_payload(case)
    values = {"color": "aabbcc", "sample_size": "10x20-cm", "done": "yes", "checked": "OK", "note": "#sample note", "brand_identifier": "source-brand"} | overrides
    lines = base64.b64decode(body["source"]["data"]).decode().splitlines()
    body["source"]["data"] = base64.b64encode((lines[0] + ";" + ";".join(values) + "\n" + lines[1] + ";" + ";".join(values.values())).encode()).decode()
    body["columns"] = {**body["columns"], **{key: key for key in values}}
    return body


def test_confirm_imports_properties_without_source_io_or_technical_approval_and_replays(access_case):
    case = access_case
    with case.client("ADMIN") as client:
        payload = confirmation(client, property_body(case))
        response = client.post("/api/material-imports/confirm", json=payload)
        assert response.status_code == 200
        result = response.json(); assert result["snapshot"]["schema_version"] == 2
        material_id = UUID(result["rows"][0]["material_id"])
        assert client.post("/api/material-imports/confirm", json=payload).json() == result
        assert client.get("/api/material-imports/" + result["id"]).json() == result
        assert case.worker.calls == []
    with case.database.session() as session:
        material = session.get(PBRMaterial, material_id); metadata = material.metadata_state
        assert material.workflow_status == "DONE" and material.checked_status == "OK" and material.note == "#sample note"
        assert material.validation_status == "NOT_CHECKED" and material.is_published is False
        assert material.automatic_file_check_status == "NOT_CHECKED" and material.automatic_file_check_complete is False
        assert material.automatic_file_checked_at is None and material.automatic_file_check_report is None
        assert (metadata.hex_color, metadata.width_cm, metadata.height_cm) == ("#AABBCC", Decimal(10), Decimal(20))
        assert metadata.source_filename is None and metadata.source_sha256 is None and metadata.source_content is None
        assert metadata.current_snapshot_id is not None and metadata.status == "WARNING"
        customer = session.get(PublishedBrand, material.published_brand_id)
        assert customer.brand_identifier == customer.customer_brand_identifier == "source-brand"
        audit = session.scalar(select(DirectoryChangeEvent).where(DirectoryChangeEvent.customer_id == customer.id))
        assert audit.after_snapshot["customer_brand_identifier"] == "source-brand"
        queued = session.scalar(select(NotionSyncState).where(NotionSyncState.entity_type == "CUSTOMER", NotionSyncState.entity_id == customer.id))
        assert queued.payload["brand_identifier"] == "source-brand" and queued.status == "PENDING"
        assert queued.revision == 1 and queued.synced_revision == 0  # Exact replay never enqueues again.
        assert session.scalar(select(func.count()).select_from(ResourceChangeEvent).where(ResourceChangeEvent.brand_id == customer.id)) == 0
        assert session.scalar(select(func.count()).select_from(MaterialApproval)) == 0


@pytest.mark.parametrize("identifier_field", ["brand_identifier", "customer_brand_identifier"])
def test_identifier_collision_blocks_before_any_import(access_case, identifier_field):
    case = access_case
    with case.database.session() as session:
        brand = session.get(PublishedBrand, case.materials[0].published_brand_id)
        session.add(PublishedBrand(company_id=brand.company_id, name="Other", folder_prefix="OTHER",
            **{"brand_identifier": "other-customer", identifier_field: "source-brand"}))
        session.commit()
    with case.client("ADMIN") as client:
        result = client.post("/api/material-imports/preview", json=property_body(case)).json()
        assert result["can_confirm"] is False
        assert "IMPORT_BRAND_IDENTIFIER_CONFLICT" in {finding["code"] for finding in result["findings"]}


def test_correction_cannot_remain_done_in_a_historical_import():
    _, findings = parse_properties({"done": "YES", "checked": "Correction"})
    assert findings == [("checked", "IMPORT_CORRECTION_REQUIRES_IN_PROGRESS")]


def test_blank_customer_identifier_does_not_clear_or_invent_an_identifier():
    values, findings = parse_properties({"brand_identifier": "  "})
    assert values == {} and findings == []


@pytest.mark.parametrize("case_name", ["blank", "unchanged", "legacy_alias"])
def test_identifier_import_only_queues_an_actual_customer_change(access_case, case_name):
    case = access_case; customer_id = case.materials[0].published_brand_id
    with case.database.session() as session:
        customer = session.get(PublishedBrand, customer_id)
        customer.customer_brand_identifier = "source-brand"
        customer.brand_identifier = "legacy-alias" if case_name == "legacy_alias" else "source-brand"
        session.commit()
    with case.client("ADMIN") as client:
        body = property_body(case, brand_identifier="" if case_name == "blank" else "source-brand")
        response = client.post("/api/material-imports/confirm", json=confirmation(client, body))
        assert response.status_code == 200
    with case.database.session() as session:
        assert session.get(PublishedBrand, customer_id).customer_brand_identifier == "source-brand"
        assert session.get(PublishedBrand, customer_id).brand_identifier == "source-brand"
        assert session.scalar(select(func.count()).select_from(NotionSyncState)) == 0
        assert session.scalar(select(func.count()).select_from(DirectoryChangeEvent)) == 0
