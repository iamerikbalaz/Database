"""Automatic check is a derived observation, never a human approval shortcut."""
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models import MaterialAuditEvent, PBRMaterial
from app.material_review import invalidate_review
from test_application_access import access_case
from test_local_files_api import local_case


def selected(case, *, both=False):
    with case.database.session() as session:
        materials = [session.get(PBRMaterial, item.id) for item in case.materials[:2 if both else 1]]
        for material in materials:
            if not material.folder_path:
                material.folder_path = "Library/" + material.technical_identity
        session.commit()
        return [{"id": str(item.id), "expected_updated_at": item.updated_at.isoformat()} for item in materials]


@pytest.mark.parametrize("issues,status", [([], "NOT_CHECKED"), (["metadata.json: MISSING"], "ISSUES")])
def test_check_persists_separate_derived_status_and_actor_without_changing_human_workflow(local_case, issues, status):
    case, library, url, _ = local_case
    with case.database.session() as session:
        material = session.get(PBRMaterial, case.materials[0].id)
        material.workflow_status = "DONE"; material.checked_status = "OK"; material.validation_status = "VALID"
        session.commit()
    library.check = lambda folder: {"report": "Preliminary result", "issues": issues, "status": "OK", "complete": True}
    with case.client("PROCESSOR") as client:
        response = client.post(url + "/check-data")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == status and body["profile"] == "BASIC_V1" and body["complete"] is False
        assert "complete automatic file checker is not configured" in body["report"]
        material = client.get(url).json()
        assert material["automatic_file_check_status"] == status
        assert material["automatic_file_checked_at"] and "automatic_file_check_report" not in material
        assert material["automatic_file_check_profile"] == "BASIC_V1" and material["automatic_file_check_complete"] is False
        assert (material["workflow_status"], material["checked_status"], material["validation_status"]) == ("DONE", "OK", "VALID")
    with case.database.session() as session:
        assert session.get(PBRMaterial, case.materials[0].id).automatic_file_check_report == body["report"]
        event = session.scalar(select(MaterialAuditEvent).where(MaterialAuditEvent.event_type == "AUTOMATIC_FILE_CHECK"))
        assert event.actor_id == case.users["PROCESSOR"].id
        assert event.result["audit"]["complete"] is False


def test_check_result_invalidates_even_without_existing_technical_review(local_case):
    case, _, url, _ = local_case
    with case.client("ADMIN") as client:
        assert client.post(url + "/check-data").status_code == 200
    with case.database.session() as session:
        material = session.get(PBRMaterial, case.materials[0].id)
        assert material.automatic_file_checked_at is not None
        invalidate_review(session, material, case.users["ADMIN"].id, "SOURCE_METADATA_STARTED")
        session.commit()
        assert material.automatic_file_check_status == "NOT_CHECKED"
        assert material.automatic_file_checked_at is None and material.automatic_file_check_report is None
        assert material.automatic_file_check_profile is None and not material.automatic_file_check_complete


@pytest.mark.parametrize("field,value", [("automatic_file_check_status", "OK"), ("automatic_file_checked_at", "2026-01-01T12:00:00Z"), ("automatic_file_check_report", "Injected approval"), ("automatic_file_check_complete", True)])
def test_users_cannot_edit_automatic_check_properties(local_case, field, value):
    case, library, url, _ = local_case
    with case.client("ADMIN") as client:
        response = client.patch(url, json={field: value}, headers={"Idempotency-Key": str(uuid4())})
        assert response.status_code == 422, response.text
    assert not library.calls


def test_bulk_checks_only_explicit_selected_materials_and_saves_one_report(local_case):
    case, library, _, folder = local_case
    saved = []
    def save(report, *, open_report):
        saved.append((report, open_report))
        return {"report_path": "C:\\Reports\\synthetic.txt", "report_opened": True}
    library.save_check_report = save
    selection = selected(case)
    with case.client("ADMIN") as client:
        response = client.post("/api/materials/check-data", json={"materials": selection})
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) == 1 and body["items"][0]["material_id"] == selection[0]["id"]
        assert body["report_opened"] and body["report_path"].endswith("synthetic.txt")
        assert "Preliminary inspection" in body["report"]
    assert library.calls == [("check", folder)]
    assert len(saved) == 1 and saved[0][1] is True and saved[0][0] == body["report"]
    with case.database.session() as session:
        assert session.get(PBRMaterial, case.materials[1].id).automatic_file_checked_at is None


def test_bulk_report_can_be_downloaded_without_desktop_and_optional_open_can_be_disabled(local_case):
    case, library, _, _ = local_case
    saved = []
    library.save_check_report = lambda report, **kwargs: saved.append(kwargs) or {"report_path": "report.txt", "report_opened": False}
    with case.client("ADMIN") as client:
        response = client.post("/api/materials/check-data", json={"materials": selected(case), "open_report": False})
        assert response.status_code == 200
        assert response.json()["report"] and not response.json()["report_opened"]
        assert saved == [{"open_report": False}]
        library.save_check_report = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("private disk path"))
        response = client.post("/api/materials/check-data", json={"materials": selected(case)})
        assert response.status_code == 200 and response.json()["report_path"] is None
        assert "private disk path" not in response.text


@pytest.mark.parametrize("variant", ["empty", "duplicate", "too_many", "missing_version", "stale_version", "foreign"])
def test_bulk_selection_is_bounded_fresh_and_fully_authorized_before_io(local_case, variant):
    case, library, _, _ = local_case
    choices = selected(case, both=variant == "foreign")
    code = 422
    if variant == "empty": choices = []
    elif variant == "duplicate": choices *= 2
    elif variant == "too_many": choices = [{"id": str(uuid4()), "expected_updated_at": choices[0]["expected_updated_at"]} for _ in range(101)]
    elif variant == "missing_version": choices[0].pop("expected_updated_at")
    elif variant == "stale_version": choices[0]["expected_updated_at"] = "2000-01-01T00:00:00Z"; code = 409
    elif variant == "foreign": code = 404
    with case.client("PROCESSOR") as client:
        response = client.post("/api/materials/check-data", json={"materials": choices})
        assert response.status_code == code, response.text
    assert not library.calls


def test_bulk_rejects_changed_material_atomically_without_report_or_partial_results(local_case):
    case, library, _, _ = local_case
    choices = selected(case, both=True)
    saved = []
    library.save_check_report = lambda *args, **kwargs: saved.append(True)
    def change_during_io():
        if len(library.calls) == 2:
            with case.database.session() as session:
                session.get(PBRMaterial, case.materials[0].id).updated_at += timedelta(seconds=1)
                session.commit()
    library.callback = change_during_io
    with case.client("ADMIN") as client:
        response = client.post("/api/materials/check-data", json={"materials": choices})
        assert response.status_code == 409, response.text
        assert "PRIVATE_CHECK_REPORT" not in response.text
    assert not saved
    with case.database.session() as session:
        assert all(item.automatic_file_checked_at is None for item in session.scalars(select(PBRMaterial)))
        assert not session.scalars(select(MaterialAuditEvent)).all()


def test_bulk_requires_authenticated_post_csrf(local_case):
    case, library, _, _ = local_case
    payload = {"materials": selected(case)}
    with case.client() as client:
        assert client.post("/api/materials/check-data", json=payload).status_code == 401
    with case.client("ADMIN") as client:
        client.headers.pop("X-CSRF-Token")
        assert client.post("/api/materials/check-data", json=payload).status_code == 403
    assert not library.calls


@pytest.mark.parametrize("issues,status", [([], "OK"), (["PREVIEW/SPHERE_1.png: expected 1200 × 1200; found 512 × 512"], "ISSUES")])
def test_full_profile_records_complete_results_without_human_approval(local_case, issues, status):
    case, library, url, _ = local_case
    library.file_check_profile = "PBR_FILES_V1"
    library.check_many = lambda folders: [{"profile": "PBR_FILES_V1", "complete": True,
        "report": "Full file check", "issues": issues} for _ in folders]
    with case.database.session() as session:
        material = session.get(PBRMaterial, case.materials[0].id)
        original = material.checked_status, material.workflow_status, material.validation_status
    with case.client("PROCESSOR") as client:
        response = client.post(url + "/check-data")
        assert response.status_code == 200, response.text
        result = response.json()
        assert (result["status"], result["profile"], result["complete"]) == (status, "PBR_FILES_V1", True)
        assert "Preliminary" not in result["report"]
    with case.database.session() as session:
        material = session.get(PBRMaterial, case.materials[0].id)
        assert (material.automatic_file_check_status, material.automatic_file_check_profile, material.automatic_file_check_complete) == (status, "PBR_FILES_V1", True)
        assert (material.checked_status, material.workflow_status, material.validation_status) == original
        event = session.scalar(select(MaterialAuditEvent).where(MaterialAuditEvent.event_type == "AUTOMATIC_FILE_CHECK"))
        assert event.actor_id == case.users["PROCESSOR"].id
        assert event.result["audit"]["complete"] is True


@pytest.mark.parametrize("bad", [[], [{}], [{"report": "Partial", "issues": [], "profile": "BASIC_V1", "complete": True}],
    [{"report": "Partial", "issues": [], "profile": "PBR_FILES_V1", "complete": False}]])
def test_full_checker_cannot_store_partial_or_mismatched_results(local_case, bad):
    case, library, url, _ = local_case
    library.file_check_profile = "PBR_FILES_V1"
    library.check_many = lambda folders: bad
    with case.client("ADMIN") as client:
        response = client.post(url + "/check-data")
        assert response.status_code == 409, response.text
    with case.database.session() as session:
        assert session.get(PBRMaterial, case.materials[0].id).automatic_file_checked_at is None
        assert not session.scalars(select(MaterialAuditEvent)).all()


def test_bulk_full_profile_runs_once_and_report_contains_only_materials_with_issues(local_case):
    case, library, _, _ = local_case
    choices = selected(case, both=True)
    library.file_check_profile = "PBR_FILES_V1"
    calls = []
    def check(folders):
        calls.append(folders)
        return [{"profile": "PBR_FILES_V1", "complete": True, "report": "CLEAN_MATERIAL_DETAIL", "issues": []},
            {"profile": "PBR_FILES_V1", "complete": True, "report": "DEFECTIVE_MATERIAL_DETAIL", "issues": ["NRM map missing"]}]
    library.check_many = check
    with case.client("ADMIN") as client:
        response = client.post("/api/materials/check-data", json={"materials": choices, "open_report": False})
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(calls) == 1 and len(calls[0]) == 2
        assert [item["status"] for item in body["items"]] == ["OK", "ISSUES"]
        assert "Checked: 2 | OK: 1 | Issues: 1 | Incomplete: 0" in body["report"]
        assert "DEFECTIVE_MATERIAL_DETAIL" in body["report"] and "CLEAN_MATERIAL_DETAIL" not in body["report"]
        assert case.materials[1].technical_identity in body["report"]


def test_clean_full_report_has_summary_without_material_details(local_case):
    case, library, _, _ = local_case
    library.file_check_profile = "PBR_FILES_V1"
    library.check_many = lambda folders: [{"profile": "PBR_FILES_V1", "complete": True, "report": "CLEAN_DETAIL", "issues": []}]
    with case.client("ADMIN") as client:
        response = client.post("/api/materials/check-data", json={"materials": selected(case), "open_report": False})
        assert response.status_code == 200, response.text
        report = response.json()["report"]
        assert "OK: 1 | Issues: 0" in report and "No issues found." in report
        assert "CLEAN_DETAIL" not in report and "Preliminary" not in report


def test_tiff_warning_stays_ok_and_is_present_in_bulk_report_and_audit(local_case):
    case, library, _, _ = local_case
    choices = selected(case, both=True)
    warning = "8K/SAFE_0001_COL_8K.tiff: legacy COL TIFF accepted; use JPG for new materials [COL_TIFF_LEGACY]"
    library.file_check_profile = "PBR_FILES_V1"
    library.check_many = lambda folders: [
        {"profile": "PBR_FILES_V1", "complete": True, "issues": [], "warnings": [warning], "report": "Status: OK\nWarnings:\n" + warning},
        {"profile": "PBR_FILES_V1", "complete": True, "issues": [], "warnings": [], "report": "ORDINARY_CLEAN_DETAIL"},
    ]
    with case.client("ADMIN") as client:
        response = client.post("/api/materials/check-data", json={"materials": choices, "open_report": False})
        assert response.status_code == 200, response.text
        body = response.json()
        assert [item["status"] for item in body["items"]] == ["OK", "OK"]
        assert body["items"][0]["warnings"] == [warning]
        assert "OK: 2 | Issues: 0" in body["report"]
        assert "Warnings — passed materials (OK)" in body["report"] and warning in body["report"]
        assert case.materials[0].technical_identity in body["report"]
        assert "ORDINARY_CLEAN_DETAIL" not in body["report"]
    with case.database.session() as session:
        material = session.get(PBRMaterial, case.materials[0].id)
        assert material.automatic_file_check_status == "OK" and warning in material.automatic_file_check_report
        event = session.scalar(select(MaterialAuditEvent).where(MaterialAuditEvent.material_id == material.id))
        assert event.result["audit"]["warnings"] == [warning]


@pytest.mark.parametrize("warnings", [None, "text", [None], [""], ["x" * 4097], ["x"] * 4097])
def test_malformed_warning_results_do_not_store_a_check(local_case, warnings):
    case, library, url, _ = local_case
    library.file_check_profile = "PBR_FILES_V1"
    library.check_many = lambda folders: [{"profile": "PBR_FILES_V1", "complete": True,
        "issues": [], "warnings": warnings, "report": "Invalid warning"}]
    with case.client("ADMIN") as client:
        assert client.post(url + "/check-data").status_code == 409
    with case.database.session() as session:
        assert session.get(PBRMaterial, case.materials[0].id).automatic_file_checked_at is None
