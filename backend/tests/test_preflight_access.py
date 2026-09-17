"""Source diagnostics must not outlive the permission that authorized their read."""
import pytest
from sqlalchemy import select

from app.db.models import InternalUser, PBRMaterial, PBRMaterialMetadataSnapshot
from app.worker_client import WorkerUnavailableError
from test_application_access import access_case
from test_material_operations import _preflight


@pytest.mark.parametrize("action", ["folder-preflight", "folder-link", "mark-done"])
@pytest.mark.parametrize("outcome", ["success", "identity", "unsafe", "unavailable"])
@pytest.mark.parametrize("change,expected", [("role", 403), ("assignment", 404), ("disabled", 401)])
def test_source_read_reauthorizes_before_success_or_error_disclosure(access_case, action, outcome, change, expected):
    case = access_case; material = case.materials[0]
    folder = f"library/{material.technical_identity}"
    if action == "mark-done":
        with case.database.session() as session:
            session.get(PBRMaterial, material.id).folder_path = folder
            session.commit()
    def during_source_read(_):
        with case.database.session() as session:
            if change == "role": session.get(InternalUser, case.users["PROCESSOR"].id).role = "LEADERSHIP"
            elif change == "disabled": session.get(InternalUser, case.users["PROCESSOR"].id).is_active = False
            else: session.get(PBRMaterial, material.id).assigned_processor_id = case.users["OTHER"].id
            session.commit()
        if outcome == "unavailable": raise WorkerUnavailableError("Synthetic controlled dependency failure")
        return _preflight("UNRELATED_0001_G03" if outcome == "identity" else material.technical_identity,
            errors=[{"code": "MATERIAL_INSPECTION_FAILED", "path": "synthetic-diagnostic", "message": "Synthetic source error"}] if outcome == "unsafe" else None)
    case.worker.preflight = during_source_read
    with case.client("PROCESSOR") as client:
        response = client.post(f"/api/materials/{material.id}/{action}", json={} if action == "mark-done" else {"folder_path": folder})
        assert response.status_code == expected
        assert all(marker not in response.text for marker in ("#A1B2C3", "UNRELATED", "synthetic-diagnostic", "Synthetic source error", "Synthetic controlled dependency failure"))
    with case.database.session() as session:
        stored = session.get(PBRMaterial, material.id)
        assert stored.workflow_status == "IN_PROGRESS" and stored.folder_path == (folder if action == "mark-done" else None)
        assert list(session.scalars(select(PBRMaterialMetadataSnapshot))) == []
