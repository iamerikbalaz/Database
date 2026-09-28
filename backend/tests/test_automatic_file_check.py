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
        assert "Final automatic validation rules are not configured" in body["report"]
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
