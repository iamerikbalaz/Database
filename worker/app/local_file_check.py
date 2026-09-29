"""One-shot full desktop checker. Source mount is read-only and network-free."""
import json
import os
from pathlib import Path
from uuid import UUID

from app.inventory import _safe_name
from app.material_file_check import check_material_files


def run(root: Path, request: dict) -> dict:
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
    for identifier, parts in parsed:
        value = check_material_files(root, parts)
        results.append({"id": identifier, **{key: value[key] for key in ("profile", "complete", "issues", "report")}})
    return {"schema_version": 1, "results": results}


def main():
    try:
        source = Path("/request/request.json")
        if source.stat().st_size > 1024 * 1024: raise ValueError("FILE_CHECK_REQUEST_INVALID")
        result = run(Path("/materials"), json.loads(source.read_bytes()))
    except Exception:
        # Infrastructure and source races are not completed material checks.
        result = {"schema_version": 1, "results": [], "error_code": "LOCAL_FILE_CHECK_FAILED"}
    with Path("/output/result.json").open("x", encoding="utf-8") as target:
        json.dump(result, target, ensure_ascii=True, indent=2)
        target.flush(); os.fsync(target.fileno())


if __name__ == "__main__": main()
