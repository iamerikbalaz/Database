"""Read the last durable automatic check without touching material files."""
from datetime import UTC, datetime
import re
import unicodedata

from sqlalchemy import select

from app.db.models import MaterialAuditEvent

MAX_REPORT = 1024 * 1024
STATUSES = {"NOT_CHECKED", "OK", "ISSUES"}


def _time(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)).isoformat()


def report_text(value, limit=MAX_REPORT):
    """Stored text is displayed literally; desktop root locations stay private.

    File findings use relative map names. Local adapters prepend a desktop
    Folder line, and older exception messages may embed drive or UNC paths.
    Reading a report never resolves, opens or trusts any of these locations.
    """
    if not isinstance(value, str):
        return ""
    text = "".join(char for char in value[:limit] if char in "\n\r\t" or not unicodedata.category(char).startswith("C"))
    text = re.sub(r"(?im)^(\s*(?:Folder|Directory|Root|Path)\s*:\s*)(?:[a-z]:[\\/]|[/\\]).*$", r"\1[material folder]", text)
    return re.sub(r"(?i)(?:\b[a-z]:[\\/]|\\\\|(?<!:)//)[^\r\n]*", "[local path]", text)


def _findings(value):
    return [report_text(item, 4096) for item in value[:4096] if isinstance(item, str)] if isinstance(value, list) else []


def last_file_check(session, material):
    query = select(MaterialAuditEvent).where(MaterialAuditEvent.material_id == material.id,
        MaterialAuditEvent.event_type == "AUTOMATIC_FILE_CHECK").order_by(MaterialAuditEvent.created_at.desc(), MaterialAuditEvent.id.desc())
    # Current row values are authoritative, including older SQLite histories
    # whose event timestamps had only whole-second precision.
    current_report = material.automatic_file_check_report
    event = None
    if current_report is not None:
        event = session.scalar(query.where(MaterialAuditEvent.result["audit"]["report"].as_string() == current_report).limit(1))
    if event is None:
        event = session.scalar(query.limit(1))
    audit = event.result.get("audit", {}) if event and isinstance(event.result, dict) else {}
    if not isinstance(audit, dict):
        audit = {}
    has_current = isinstance(current_report, str) and material.automatic_file_checked_at is not None
    if not has_current and not isinstance(audit.get("report"), str):
        return {"material_id": str(material.id), "current_status": material.automatic_file_check_status, "report": None}
    current = has_current
    if current and audit.get("report") != current_report:
        audit = {}  # Legacy current report with no matching structured evidence.
    if current:
        status = material.automatic_file_check_status
        checked_at = _time(material.automatic_file_checked_at)
        profile = material.automatic_file_check_profile
        complete = material.automatic_file_check_complete
        text = current_report
    else:
        values = audit.get("values") or {}
        status = values.get("automatic_file_check_status") if isinstance(values, dict) else None
        checked_at = _time(audit.get("checked_at")) or _time(event.created_at if event else None)
        profile = audit.get("profile")
        complete = audit.get("complete") is True
        text = audit.get("report", "")
    return {"material_id": str(material.id), "current_status": material.automatic_file_check_status,
        "report": {"status": status if status in STATUSES else "NOT_CHECKED", "checked_at": checked_at,
            "profile": report_text(profile, 32) if isinstance(profile, str) else None, "complete": bool(complete),
            "is_current": bool(current), "issues": _findings(audit.get("issues")), "warnings": _findings(audit.get("warnings")),
            "text": report_text(text)}}
