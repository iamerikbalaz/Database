"""Scoped, read-only source inspection, separate from human and technical approval.

The agreed final rules do not exist yet. BASIC_V1 can report observed issues but
cannot certify a material as OK. A clean preliminary run stays NOT_CHECKED.
"""
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, model_validator

from app.api.material_review import _material
from app.auth.access import MATERIAL_EDITORS
from app.auth.service import database_now
from app.db.models import MaterialAuditEvent
from app.discovery_client import DiscoveryClientError
from app.local_filesystem import LocalFilesError
from app.material_identity import require_material_idle
from app.metadata_client import MetadataClientError
from app.preview_client import PreviewClientError
from app.schemas import ApiSchema

PROFILE = "BASIC_V1"
LIMITATION = "Preliminary inspection only. Final automatic validation rules are not configured; this result does not certify publication readiness."


class CheckSelection(ApiSchema):
    id: UUID
    expected_updated_at: datetime | None = None


class BulkFileCheck(ApiSchema):
    materials: list[CheckSelection] = Field(min_length=1, max_length=100)
    open_report: bool = True

    @model_validator(mode="after")
    def distinct_selection(self):
        if len({item.id for item in self.materials}) != len(self.materials):
            raise ValueError("Select each material once")
        if any(item.expected_updated_at is None for item in self.materials):
            raise ValueError("Selected material versions are required")
        return self


def _aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _context(session, selection, access, *, lock=False):
    material = _material(session, selection.id, access, lock=lock, historical=access.user.role == "ADMIN")
    require_material_idle(session, material.id)
    if not material.folder_path:
        raise HTTPException(409, {"code": "FOLDER_REQUIRED"})
    access.require_folder(material.folder_path, material.technical_identity)
    if selection.expected_updated_at is not None and _aware(material.updated_at) != _aware(selection.expected_updated_at):
        raise HTTPException(409, {"code": "LOCAL_MATERIAL_CHANGED"})
    return material, (material.folder_path, material.technical_identity, material.updated_at)


def _result(value):
    if not isinstance(value, dict) or not isinstance(value.get("report"), str):
        raise ValueError("Invalid check result")
    issues = value.get("issues")
    if not isinstance(issues, list) or len(issues) > 4096 or any(not isinstance(item, str) or len(item) > 4096 for item in issues):
        raise ValueError("Invalid check issues")
    if len(value["report"]) > 1024 * 1024:
        raise ValueError("Invalid check report")
    # The adapter cannot silently promote a preliminary profile into final OK.
    return {"status": "ISSUES" if issues else "NOT_CHECKED", "profile": PROFILE,
        "complete": False, "issues": issues, "report": value["report"] + "\n\n" + LIMITATION}


def check_materials(database, library, access, selections):
    """Inspect all selected sources; reauthorize and commit results atomically.

    Authorization and current versions are checked for the whole selection before
    any source IO. No source path comes from the caller. An IO failure or any
    intervening source/assignment/status change prevents storing partial results.
    """
    if library is None:
        raise HTTPException(503, {"code": "LOCAL_DESKTOP_UNAVAILABLE"})
    if not 1 <= len(selections) <= 100 or len({item.id for item in selections}) != len(selections):
        raise HTTPException(422, {"code": "FILE_CHECK_SELECTION_INVALID"})
    expected = {}
    with database.session() as session:
        access.check(session, MATERIAL_EDITORS)
        for selection in sorted(selections, key=lambda item: str(item.id)):
            _, expected[selection.id] = _context(session, selection, access)
    results = {}; failed = False
    try:
        for selection in selections:
            results[selection.id] = _result(library.check(expected[selection.id][0]))
    except (OSError, ValueError, LocalFilesError, MetadataClientError, PreviewClientError, DiscoveryClientError):
        failed = True
    with database.session() as session:
        access.check(session, MATERIAL_EDITORS)
        materials = {}
        for selection in sorted(selections, key=lambda item: str(item.id)):
            material, current = _context(session, selection, access, lock=True)
            if current != expected[selection.id]:
                raise HTTPException(409, {"code": "LOCAL_MATERIAL_CHANGED"})
            materials[selection.id] = material
        if failed:
            raise HTTPException(409, {"code": "LOCAL_FILE_ACTION_FAILED"})
        checked_at = database_now(session)
        for selection in selections:
            material = materials[selection.id]; result = results[selection.id]
            material.automatic_file_check_status = result["status"]
            material.automatic_file_checked_at = checked_at
            material.automatic_file_check_report = result["report"]
            material.automatic_file_check_profile = PROFILE
            material.automatic_file_check_complete = False
            session.add(MaterialAuditEvent(material_id=material.id, actor_id=access.user.id,
                event_type="AUTOMATIC_FILE_CHECK", generation=0, revision_hash=None,
                result={"audit": {"values": {"automatic_file_check_status": result["status"]},
                    "profile": PROFILE, "complete": False, "issues": result["issues"], "report": result["report"]}}))
            result.update(material_id=str(material.id), checked_at=_aware(checked_at).isoformat())
        session.commit()
        for selection in selections:
            results[selection.id]["updated_at"] = _aware(materials[selection.id].updated_at).isoformat()
    return [results[item.id] for item in selections]


def combined_report(items):
    return "\n\n".join(["REAWOTE — Automatic file check", LIMITATION,
        *[f"Material: {item['material_id']}\nStatus: {item['status']}\nChecked: {item['checked_at']}\n{item['report']}" for item in items]]) + "\n"
