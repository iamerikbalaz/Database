"""Checked storage locations; editing a root never moves existing data."""
from pathlib import Path
import os
import stat
from sqlalchemy import select, text
from app.db.path_settings_models import PathsSettingsRevision

PATHS_LOCK = 737824936


class PathSettingsError(ValueError):
    pass


def lock_paths(session, *, exclusive=False):
    if session.get_bind().dialect.name == "postgresql":
        function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
        session.execute(text(f"SELECT {function}(:key)"), {"key": PATHS_LOCK})


def current_paths(session, runtime):
    saved = session.scalar(select(PathsSettingsRevision).order_by(PathsSettingsRevision.version.desc()).limit(1))
    if saved:
        return dict(saved.response_snapshot)
    return {"version": 0, "sbs_templates_root": getattr(runtime, "sbs_templates_root", r"C:\Users\Admin\Desktop\Substance graphy vzory"),
        "orders_root": runtime.order_folders_root or r"R:\0. PROJECTS", "materials_root": getattr(runtime, "materials_root", r"C:\Users\Admin\Desktop\Test_data")}


def checked_directory(value):
    from app.order_folders import checked_root, OrderFolderError
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        raise PathSettingsError("Folder paths cannot contain control characters.")
    path = Path(value)
    if not value or not path.is_absolute() or ".." in path.parts or path == path.parent:
        raise PathSettingsError("Use an absolute folder path, not a drive root or a parent traversal.")
    try:
        checked_root(value)
    except (OrderFolderError, OSError):
        raise PathSettingsError("The folder must exist and must not contain symlinks or junctions.") from None
    return path


def validate_paths(values, runtime):
    roots = {key: checked_directory(values[key]) for key in ("sbs_templates_root", "orders_root", "materials_root")}
    allowed = checked_directory(runtime.materials_root)
    if not roots["materials_root"].is_relative_to(allowed):
        raise PathSettingsError("Material data must stay inside the configured Test_data root during testing.")
    for left, right in (("sbs_templates_root", "orders_root"), ("sbs_templates_root", "materials_root"), ("orders_root", "materials_root")):
        if roots[left].is_relative_to(roots[right]) or roots[right].is_relative_to(roots[left]):
            raise PathSettingsError("Templates, orders and materials must use separate folders.")
    return {key: str(path) for key, path in roots.items()}


def resolve_sbs_template(root, name):
    directory = checked_directory(root)
    if not isinstance(name, str) or not name or any(ord(char) < 32 for char in name) or Path(name).name != name or any(c in name for c in '/\\:') or Path(name).suffix.lower() != ".sbs":
        raise PathSettingsError("Choose an SBS template from the configured folder.")
    candidate = directory / name
    try:
        info = candidate.lstat()
    except OSError:
        raise PathSettingsError("The SBS template is unavailable.") from None
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
        raise PathSettingsError("The SBS template must be a regular file, not a link.")
    if not os.access(candidate, os.R_OK):
        raise PathSettingsError("The SBS template is not readable.")
    return candidate


def list_sbs_templates(root):
    directory = checked_directory(root)
    try:
        entries = sorted(directory.iterdir(), key=lambda path: path.name.casefold())
        results = []
        for entry in entries:
            if entry.suffix.lower() != ".sbs":
                continue
            try:
                path = resolve_sbs_template(str(directory), entry.name)
                results.append({"name": entry.name, "size_bytes": path.stat().st_size})
            except (PathSettingsError, OSError):
                continue
        return results
    except OSError:
        raise PathSettingsError("The SBS template folder is unavailable.") from None
