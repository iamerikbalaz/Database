"""One-shot full desktop checker. Source mount is read-only and network-free."""
import json
import os
from pathlib import Path
from uuid import UUID

from app.inventory import InventoryError, _safe_name
from app.material_file_check import check_material_files
from app.secure_filesystem import (MaterialFolderNotFound, MaterialsRootUnavailable,
    SecureFilesystemAccessUnavailable, UnsafeMaterialPath)


SAFE_INVENTORY_ERRORS = frozenset({"INVENTORY_TIME_LIMIT", "INVENTORY_DEPTH_LIMIT", "INVENTORY_ENTRY_LIMIT",
    "INVENTORY_UNSAFE_NAME", "INVENTORY_PATH_LIMIT", "INVENTORY_UNSAFE_ENTRY", "INVENTORY_SOURCE_CHANGED",
    "INVENTORY_FILE_LIMIT", "INVENTORY_TOTAL_LIMIT", "INVENTORY_READ_FAILED", "FILE_CHECK_INCOMPLETE", "VALIDATION_BUSY"})


def safe_error_code(error):
    if isinstance(error, InventoryError) and str(error) in SAFE_INVENTORY_ERRORS: return str(error)
    for kind, code in ((MaterialFolderNotFound, "MATERIAL_FOLDER_NOT_FOUND"),
        (MaterialsRootUnavailable, "MATERIALS_ROOT_UNAVAILABLE"),
        (SecureFilesystemAccessUnavailable, "SECURE_FILESYSTEM_ACCESS_UNAVAILABLE"),
        (UnsafeMaterialPath, "UNSAFE_MATERIAL_PATH")):
        if isinstance(error, kind): return code
    if isinstance(error, ValueError) and str(error) in {"FILE_CHECK_REQUEST_INVALID", "FILE_CHECK_PATH_INVALID"}: return str(error)
    return "LOCAL_FILE_CHECK_FAILED"


class FileCheckRunError(RuntimeError):
    def __init__(self, error, identifier, index, total):
        self.code = safe_error_code(error)
        self.material_id = identifier
        self.material_index = index
        self.total = total
        super().__init__(self.code)


def run(root: Path, request: dict, *, progress=None) -> dict:
    if not isinstance(request, dict) or set(request) != {"schema_version", "materials"} or type(request["schema_version"]) is not int or request["schema_version"] != 1:
        raise ValueError("FILE_CHECK_REQUEST_INVALID")
    materials = request["materials"]
    if not isinstance(materials, list) or not 1 <= len(materials) <= 100:
        raise ValueError("FILE_CHECK_REQUEST_INVALID")
    seen = set(); parsed = []
    for item in materials:
        if not isinstance(item, dict) or set(item) != {"id", "folder_path"} or not isinstance(item["id"], str) or not isinstance(item["folder_path"], str):
            raise ValueError("FILE_CHECK_REQUEST_INVALID")
        identifier = str(UUID(item["id"]))
        if identifier != item["id"] or identifier in seen: raise ValueError("FILE_CHECK_REQUEST_INVALID")
        seen.add(identifier)
        parts = tuple(item["folder_path"].split("/"))
        if not parts or len(item["folder_path"].encode("utf-8")) > 2048 or not all(_safe_name(part) for part in parts):
            raise ValueError("FILE_CHECK_REQUEST_INVALID")
        parsed.append((identifier, parts))
    results = []
    for index, (identifier, parts) in enumerate(parsed, start=1):
        if progress is not None:
            progress({"schema_version": 1, "material_id": identifier, "material_index": index, "total": len(parsed)})
        try:
            value = check_material_files(root, parts)
        except Exception as error:
            raise FileCheckRunError(error, identifier, index, len(parsed)) from None
        results.append({"id": identifier, **{key: value[key] for key in ("profile", "complete", "issues", "report")}})
    return {"schema_version": 1, "results": results}


def main():
    def write_progress(value):
        # Private diagnostics contain only validated request UUIDs and counts.
        # No filenames, source bytes, environment or exception strings escape.
        temporary = Path("/output/progress.tmp")
        with temporary.open("w", encoding="utf-8") as target:
            json.dump(value, target, ensure_ascii=True)
            target.flush(); os.fsync(target.fileno())
        temporary.replace("/output/progress.json")
    try:
        source = Path("/request/request.json")
        if source.stat().st_size > 1024 * 1024: raise ValueError("FILE_CHECK_REQUEST_INVALID")
        result = run(Path("/materials"), json.loads(source.read_bytes()), progress=write_progress)
    except FileCheckRunError as error:
        result = {"schema_version": 1, "results": [], "error_code": error.code,
            "material_id": error.material_id, "material_index": error.material_index, "total": error.total}
    except Exception as error:
        # Infrastructure and source races are not completed material checks.
        result = {"schema_version": 1, "results": [], "error_code": safe_error_code(error)}
    with Path("/output/result.json").open("x", encoding="utf-8") as target:
        json.dump(result, target, ensure_ascii=True, indent=2)
        target.flush(); os.fsync(target.fileno())


if __name__ == "__main__": main()
