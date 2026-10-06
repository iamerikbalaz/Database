"""Stored reports survive process restarts and never rerun source inspection."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.db.models import MaterialAuditEvent, PBRMaterial
from app.main import create_app
from app.material_review import invalidate_review
from app.stored_file_check import report_text
from test_application_access import access_case  # noqa: F401
from test_local_files_api import local_case  # noqa: F401
from test_automatic_file_check import selected


def full_library(library):
    library.file_check_profile = "PBR_FILES_V1"
    def check_many(folders):
        library.calls.extend(("check_many", folder) for folder in folders)
        return [{"profile": "PBR_FILES_V1", "complete": True, "issues": ["8K/COL.jpg: missing"],
            "warnings": ["Older COL TIFF accepted"], "report": "Folder: C:\\PRIVATE\\Material\nMaterial: " + folder.rsplit("/", 1)[-1] + "\n8K/COL.jpg: missing"} for folder in folders]
    library.check_many = check_many


def counts(database):
    with database.session() as session:
        return session.scalar(select(func.count()).select_from(MaterialAuditEvent))


def test_bulk_report_survives_new_app_without_library_and_is_readonly(local_case):
    case, library, path, _ = local_case
    full_library(library)
    with case.client("ADMIN") as client:
        checked = client.post("/api/materials/check-data", json={"materials": selected(case, both=True), "open_report": False})
        assert checked.status_code == 200, checked.text
        checked_at = checked.json()["items"][0]["checked_at"]
    call_count = len(library.calls); audit_count = counts(case.database)
    case.app = create_app(case.app.state.settings, case.database, case.worker)
    with case.client("ADMIN") as client:
        for material in case.materials:
            result = client.get(f"/api/materials/{material.id}/automatic-file-check-report")
            assert result.status_code == 200, result.text
            assert result.headers["cache-control"] == "no-store"
            body = result.json()
            assert body["current_status"] == "ISSUES"
            assert body["report"]["status"] == "ISSUES" and body["report"]["is_current"] is True
            assert body["report"]["checked_at"] == checked_at
            assert body["report"]["profile"] == "PBR_FILES_V1" and body["report"]["complete"] is True
            assert body["report"]["issues"] == ["8K/COL.jpg: missing"]
            assert body["report"]["warnings"] == ["Older COL TIFF accepted"]
            assert "PRIVATE" not in body["report"]["text"] and "8K/COL.jpg" in body["report"]["text"]
        assert client.get(path).json()["automatic_file_check_status"] == "ISSUES"
    assert len(library.calls) == call_count and counts(case.database) == audit_count


def test_invalidation_keeps_last_report_as_historical_and_archive_admin_can_read(local_case):
    case, library, path, _ = local_case
    full_library(library)
    with case.client("ADMIN") as client:
        assert client.post(path + "/check-data").status_code == 200
        original = client.get(path + "/automatic-file-check-report").json()["report"]
        with case.database.session() as session:
            material = session.get(PBRMaterial, case.materials[0].id)
            invalidate_review(session, material, case.users["ADMIN"].id, "CONTENT_CHANGED")
            session.commit()
        historical = client.get(path + "/automatic-file-check-report").json()
        assert historical["current_status"] == "NOT_CHECKED"
        assert historical["report"] == {**original, "is_current": False}
        from test_material_archives import apply as archive, prepare as prepare_archive
        assert archive(client, case.materials[0].id, prepare_archive(client, case.materials[0].id)).status_code == 200
        assert client.get(path + "/automatic-file-check-report").json()["report"]["text"] == original["text"]
    with case.client("PROCESSOR") as client:
        assert client.get(path + "/automatic-file-check-report").status_code == 404


@pytest.mark.parametrize("role,status", [(None, 401), ("OTHER", 404), ("PROCESSOR", 200), ("ADMIN", 200), ("PRODUCTION_LEAD", 200), ("LEADERSHIP", 200)])
def test_report_uses_authenticated_material_scope_and_never_requires_desktop(local_case, role, status):
    case, library, path, _ = local_case
    with case.client(role) as client:
        result = client.get(path + "/automatic-file-check-report")
        assert result.status_code == status
        if status == 200:
            assert result.json() == {"material_id": str(case.materials[0].id), "current_status": "NOT_CHECKED", "report": None}
    assert not library.calls


def test_deleted_material_report_is_not_disclosed(local_case):
    case, library, path, _ = local_case
    full_library(library)
    with case.client("ADMIN") as client:
        assert client.post(path + "/check-data").status_code == 200
        with case.database.session() as session:
            session.get(PBRMaterial, case.materials[0].id).deleted_at = datetime.now(UTC)
            session.commit()
        assert client.get(path + "/automatic-file-check-report").status_code == 404


def test_legacy_report_without_matching_audit_is_preserved_without_wrong_findings(local_case):
    case, _, path, _ = local_case
    with case.database.session() as session:
        material = session.get(PBRMaterial, case.materials[0].id)
        material.automatic_file_check_status = "ISSUES"
        material.automatic_file_check_report = "Legacy issue text"
        material.automatic_file_checked_at = datetime.now(UTC)
        material.automatic_file_check_profile = "BASIC_V1"
        session.add(MaterialAuditEvent(material_id=material.id, actor_id=case.users["ADMIN"].id, event_type="AUTOMATIC_FILE_CHECK", generation=0,
            result={"audit": {"report": "Different older report", "issues": ["Different older issue"]}}))
        session.commit()
    with case.client("ADMIN") as client:
        report = client.get(path + "/automatic-file-check-report").json()["report"]
        assert report["text"] == "Legacy issue text" and report["is_current"] is True
        assert report["issues"] == [] and report["complete"] is False


def test_report_sanitization_preserves_map_findings_and_removes_absolute_paths():
    source = "Folder: /private/material\n8K/COL.jpg: bad dimensions\nError C:\\Private root\\file.tif\nUNC \\\\NAS\\Private\\name\n<img src=x>\x00"
    text = report_text(source)
    assert "/private" not in text and "Private" not in text and "NAS" not in text and "\x00" not in text
    assert "8K/COL.jpg: bad dimensions" in text and "<img src=x>" in text
    assert len(report_text("a" * 100, 20)) == 20
