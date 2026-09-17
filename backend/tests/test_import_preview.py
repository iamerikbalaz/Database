"""Database-backed import previews preserve exact identities and reference intent."""
import base64
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.db.models import Company, InternalUser, MaterialFileOperation, MaterialNumberReservation, PBRMaterial, Project, PublishedBrand
from test_application_access import access_case
from test_import_requests import COLUMNS

PATH = "/api/material-imports/preview"


def plan_payload(case, identities=("SAFE_0007_G03",), extra=""):
    material = case.materials[0]
    content = "Identity;Name;Project;Brand;Processor" + (";Unused" if extra else "") + "\n"
    content += "\n".join(identity + ";Synthetic import;Project A;Brand A;Processor A" + (";" + extra if extra else "") for identity in identities)
    return {"source": {"format": "CSV", "delimiter": ";", "data": base64.b64encode(content.encode()).decode()},
            "columns": COLUMNS, "links": {"projects": {"Project A": str(material.project_id)},
                "brands": {"Brand A": str(material.published_brand_id)},
                "processors": {"Processor A": str(material.assigned_processor_id)}}}


def test_preview_is_read_only_exact_stable_and_separates_project_and_brand_companies(access_case):
    case = access_case
    with case.database.session() as session:
        company = Company(name="Independent project company")
        session.add(company); session.flush()
        session.get(Project, case.materials[0].project_id).company_id = company.id
        session.commit()
    with case.client("ADMIN") as client:
        body = plan_payload(case, extra="Ignored historical statement")
        result = client.post(PATH, json=body)
        assert result.status_code == 200
        preview = result.json()
        assert client.post(PATH, json=body).json() == preview
        assert preview["can_confirm"] and len(preview["preview_hash"]) == 64
        assert preview["findings"] == [] and preview["warnings"] == ["IMPORT_REQUIRES_NORMAL_REVIEW"]
        snapshot = preview["snapshot"]
        assert snapshot["rows"][0]["technical_identity"] == "SAFE_0007_G03"
        assert snapshot["rows"][0]["sequence_number"] == 7
        assert snapshot["ignored_columns"] == ["Unused"]
        assert "Ignored historical statement" not in str(preview)
        assert snapshot["references"]["projects"][0]["company_id"] != snapshot["references"]["brands"][0]["company_id"]
        assert len(snapshot["references"]["companies"]) == 2
        assert snapshot["initial_state"] == {"workflow_status": "IN_PROGRESS", "validation_status": "NOT_CHECKED",
                                              "publication_status": "NOT_PUBLISHED", "is_published": False, "folder_path": None}
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(PBRMaterial)) == 2
        assert session.get(PublishedBrand, case.materials[0].published_brand_id).next_sequence_number == 3


@pytest.mark.parametrize("change,code", [
    ("project_missing", "IMPORT_PROJECT_MISSING"), ("brand_missing", "IMPORT_BRAND_MISSING"),
    ("brand_inactive", "IMPORT_BRAND_INACTIVE"), ("prefix", "IMPORT_BRAND_PREFIX_MISMATCH"),
    ("processor_missing", "IMPORT_PROCESSOR_UNAVAILABLE"), ("processor_role", "IMPORT_PROCESSOR_UNAVAILABLE"),
    ("processor_inactive", "IMPORT_PROCESSOR_UNAVAILABLE"), ("company", "IMPORT_COMPANY_UNAVAILABLE"),
    ("reserved", "IMPORT_NUMBER_RESERVED"), ("active_operation", "IMPORT_BRAND_OPERATION_ACTIVE"),
])
def test_current_reference_and_identity_constraints_block_the_entire_import(access_case, change, code):
    case = access_case; material = case.materials[0]; body = plan_payload(case)
    with case.database.session() as session:
        brand = session.get(PublishedBrand, material.published_brand_id)
        if change == "project_missing": body["links"]["projects"]["Project A"] = str(uuid4())
        elif change == "brand_missing": body["links"]["brands"]["Brand A"] = str(uuid4())
        elif change == "brand_inactive": brand.is_active = False
        elif change == "prefix": brand.folder_prefix = "OTHER"
        elif change == "processor_missing": body["links"]["processors"]["Processor A"] = str(uuid4())
        elif change == "processor_role": session.get(InternalUser, material.assigned_processor_id).role = "LEADERSHIP"
        elif change == "processor_inactive": session.get(InternalUser, material.assigned_processor_id).is_active = False
        elif change == "company": session.get(Company, brand.company_id).is_active = False
        elif change == "reserved": session.add(MaterialNumberReservation(brand_id=brand.id, sequence_number=7, material_id=material.id))
        else:
            session.add(MaterialFileOperation(material_id=material.id, actor_id=case.users["ADMIN"].id,
                source_brand_id=brand.id, target_brand_id=brand.id, request_key=uuid4(), request_hash="a" * 64,
                proposal_hash="b" * 64, request_payload={}, source_context={}, target_context={}, worker_plan={}, status="RUNNING"))
        session.commit()
    with case.client("ADMIN") as client:
        result = client.post(PATH, json=body)
        assert result.status_code == 200
        preview = result.json()
        assert not preview["can_confirm"] and preview["preview_hash"] is None
        assert code in {finding["code"] for finding in preview["findings"]}
        assert all(finding["row"] == 2 for finding in preview["findings"])


@pytest.mark.parametrize("identities,code", [
    (("SAFE_0007_G03", "SAFE_0001_G04"), "IMPORT_MATERIAL_EXISTS"),
    (("SAFE_0007_G03", "SAFE_0007_G04"), "IMPORT_DUPLICATE_IDENTITY_OR_NUMBER"),
    (("SAFE_0007_G03", "SAFE_7_G03"), "IMPORT_IDENTITY_FORMAT"),
])
def test_one_bad_row_never_yields_a_confirmable_partial_import(access_case, identities, code):
    with access_case.client("ADMIN") as client:
        preview = client.post(PATH, json=plan_payload(access_case, identities)).json()
        assert preview["row_count"] == 2 and not preview["can_confirm"] and preview["preview_hash"] is None
        assert code in {finding["code"] for finding in preview["findings"]}


@pytest.mark.parametrize("field", ["company_name", "project_name", "brand_name", "brand_counter", "processor_name", "source", "delimiter"])
def test_preview_digest_binds_source_options_and_current_reference_context(access_case, field):
    case = access_case; material = case.materials[0]; body = plan_payload(case)
    with case.client("ADMIN") as client:
        before = client.post(PATH, json=body).json()
        with case.database.session() as session:
            brand = session.get(PublishedBrand, material.published_brand_id)
            if field == "company_name": session.get(Company, brand.company_id).name = "Corrected company"
            elif field == "project_name": session.get(Project, material.project_id).name = "Corrected project"
            elif field == "brand_name": brand.name = "Corrected brand"
            elif field == "brand_counter": brand.next_sequence_number += 1
            elif field == "processor_name": session.get(InternalUser, material.assigned_processor_id).display_name = "Corrected processor"
            elif field == "source": body = plan_payload(case, extra="Unmapped column still binds source digest")
            else:
                body["source"]["data"] = base64.b64encode(base64.b64decode(body["source"]["data"]).replace(b";", b",")).decode()
                body["source"]["delimiter"] = ","
            session.commit()
        after = client.post(PATH, json=body).json()
        assert before["can_confirm"] and after["can_confirm"]
        assert before["preview_hash"] != after["preview_hash"]


@pytest.mark.parametrize("role,status", [(None, 401), ("PROCESSOR", 403), ("PRODUCTION_LEAD", 403), ("LEADERSHIP", 403)])
def test_only_administrator_can_resolve_import_references(access_case, role, status):
    with access_case.client(role) as client:
        result = client.post(PATH, json=plan_payload(access_case))
        assert result.status_code == status and "snapshot" not in result.text
