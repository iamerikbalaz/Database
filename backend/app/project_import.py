"""Read-only project folder parsing and deterministic import planning.

No filesystem access, database writes, inferred completion status or source rename.
The caller supplies observed immediate children and existing database projects.
"""
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import PureWindowsPath
import re

_PATTERN = re.compile(r"^(?P<number>[0-9]{4})_(?P<manufacturer>[^_\\/:*?\"<>|]+)_(?P<job_type>[^_\\/:*?\"<>|]+)_(?P<specifier>[^_\\/:*?\"<>|]+)_(?P<month>0[1-9]|1[0-2])(?P<year>[0-9]{4})$")


@dataclass(frozen=True)
class ProjectFolder:
    project_number: str
    name: str
    manufacturer: str
    job_type: str
    material_specifier: str | None
    folder_month: str
    folder_path: str
    warning: str | None = None


def manufacturer_key(value: str) -> str:
    return re.sub(r"[\s-]+", " ", value.strip()).casefold()


def parse_project_folder(name: str, path: str, root: str, *, allow_legacy: bool = False) -> ProjectFolder:
    legacy = allow_legacy and name.count("_") == 3
    match_name = name.rsplit("_", 1)[0] + "_LEGACY-MISSING-SPECIFIER_" + name.rsplit("_", 1)[1] if legacy else name
    if any(ord(character) < 32 for character in name + path + root):
        raise ValueError("INVALID_PROJECT_FOLDER")
    match = _PATTERN.fullmatch(match_name)
    folder, base = PureWindowsPath(path), PureWindowsPath(root)
    if not match or len(name) > 255 or not folder.is_absolute() or not base.is_absolute():
        raise ValueError("INVALID_PROJECT_FOLDER")
    if folder.name != name or folder.parent != base or ".." in folder.parts or ".." in base.parts:
        raise ValueError("PROJECT_FOLDER_OUTSIDE_ROOT")
    fields = match.groupdict()
    if any(value != value.strip() or not value.strip() for value in fields.values()) or int(fields["year"]) < 1900:
        raise ValueError("INVALID_PROJECT_FOLDER")
    return ProjectFolder(fields["number"], name, fields["manufacturer"], fields["job_type"],
        None if legacy else fields["specifier"], f'{fields["year"]}-{fields["month"]}', str(folder), "LEGACY_MISSING_SPECIFIER" if legacy else None)


def plan_project_import(folders: list[dict], existing_projects: list[dict], *, root: str, allow_legacy: bool = False) -> dict:
    """Return create/link/existing/conflicts; never silently overwrite existing data.

    Existing rows use API keys id/project_number/folder_path. A matching number
    without a folder can be linked only after caller explicitly reviews `link`.
    A different folder or duplicate number is a conflict, never a second project.
    """
    parsed, conflicts = [], []
    for entry in sorted(folders, key=lambda row: (row.get("name", "").casefold(), row.get("path", "").casefold())):
        try:
            parsed.append(parse_project_folder(entry["name"], entry["path"], root, allow_legacy=allow_legacy))
        except (ValueError, KeyError) as exc:
            conflicts.append({"folder": entry, "reason": str(exc)})
    counts = Counter(row.project_number for row in parsed)
    existing = {}
    for row in existing_projects:
        existing.setdefault(str(row["project_number"]), []).append(row)
    result = {"create": [], "link": [], "existing": [], "conflicts": conflicts}
    for row in parsed:
        record = asdict(row)
        matches = existing.get(row.project_number, [])
        if counts[row.project_number] > 1 or len(matches) > 1:
            result["conflicts"].append({"folder": record, "reason": "DUPLICATE_PROJECT_NUMBER"})
        elif not matches:
            result["create"].append(record)
        elif not matches[0].get("folder_path"):
            result["link"].append({**record, "id": matches[0]["id"]})
        elif PureWindowsPath(matches[0]["folder_path"]) == PureWindowsPath(row.folder_path):
            result["existing"].append({**record, "id": matches[0]["id"]})
        else:
            result["conflicts"].append({"folder": record, "id": matches[0]["id"], "reason": "PROJECT_NUMBER_FOLDER_CONFLICT"})
    return result
