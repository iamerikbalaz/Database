"""Historical spreadsheet properties keep units, provenance and exact retries."""
import base64
from decimal import Decimal
from uuid import UUID
import pytest
from sqlalchemy import func, select
from app.db.models import MaterialApproval, PBRMaterial, PublishedBrand
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
    ("checked", "YES", "IMPORT_CHECKED_REQUIRES_DONE"), ("brand_identifier", "", "IMPORT_BRAND_IDENTIFIER_INVALID")])
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
        assert (metadata.hex_color, metadata.width_cm, metadata.height_cm) == ("#AABBCC", Decimal(10), Decimal(20))
        assert metadata.source_filename is None and metadata.source_sha256 is None and metadata.source_content is None
        assert metadata.current_snapshot_id is not None and metadata.status == "WARNING"
        assert session.get(PublishedBrand, material.published_brand_id).brand_identifier == "source-brand"
        assert session.scalar(select(func.count()).select_from(MaterialApproval)) == 0


def test_identifier_collision_blocks_before_any_import(access_case):
    case = access_case
    with case.database.session() as session:
        brand = session.get(PublishedBrand, case.materials[0].published_brand_id)
        session.add(PublishedBrand(company_id=brand.company_id, name="Other", folder_prefix="OTHER", brand_identifier="source-brand"))
        session.commit()
    with case.client("ADMIN") as client:
        result = client.post("/api/material-imports/preview", json=property_body(case)).json()
        assert result["can_confirm"] is False
        assert "IMPORT_BRAND_IDENTIFIER_CONFLICT" in {finding["code"] for finding in result["findings"]}


def test_correction_cannot_remain_done_in_a_historical_import():
    _, findings = parse_properties({"done": "YES", "checked": "Correction"})
    assert findings == [("checked", "IMPORT_CORRECTION_REQUIRES_IN_PROGRESS")]
