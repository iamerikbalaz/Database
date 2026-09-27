"""Global automatic ZIP selection; saved material decisions remain immutable."""
from datetime import date
from sqlalchemy import select, text

from app.db.models import PackagingSettingsRevision

SETTINGS_LOCK = 737824935


def lock_settings(session, *, exclusive=False):
    # Always before material locks. Settings edits never acquire material locks.
    if session.get_bind().dialect.name == "postgresql":
        function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
        session.execute(text(f"SELECT {function}(:key)"), {"key": SETTINGS_LOCK})


def current_settings(session, runtime):
    saved = session.scalar(select(PackagingSettingsRevision).order_by(PackagingSettingsRevision.version.desc()).limit(1))
    return {"version": saved.version if saved else 0,
        "cutoff_date": (saved.cutoff_date if saved else date(2026, 3, 4)).isoformat(),
        "storage_timezone": saved.storage_timezone if saved else runtime.zip_policy_timezone,
        "before_method": "A", "on_or_after_method": "B",
        "legacy_zip_timestamp": "2026-01-01T00:00:00",
        "method_a": "Normalize ZIP dates to 1 January 2026",
        "method_b": "Retain packaging dates"}
