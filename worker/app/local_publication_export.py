"""One-shot offline desktop export using the existing verified ZIP implementation.

The desktop host supplies only frozen database values and mounts its library
read-only. This command neither uploads nor mutates source materials.
"""
from datetime import date, datetime, time
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.inventory import _safe_name
from app.packaging_assembly import assemble_packages
from app.packaging_stage import stage_packaging_inputs
from app.preflight import ZipPolicy
from app.technical_validation import validate_material


def export_material(root, workspace, output, item, config):
    parts = tuple(item["folder_path"].split("/"))
    if not parts or not all(_safe_name(part) for part in parts) or parts[-1] != item["identity_name"]:
        raise ValueError("LOCAL_EXPORT_SOURCE_INVALID")
    report = validate_material(root, parts)
    if not report["can_approve"]:
        return {"material_id": item["material_id"], "status": "FAILED", "issues": report["errors"], "archives": []}
    inventory = report["inventory"]
    metadata = next((entry for entry in inventory["entries"] if entry["path"] == "metadata.json"), None)
    if metadata is None or metadata["sha256"] != item["metadata_sha256"]:
        raise ValueError("LOCAL_EXPORT_METADATA_CHANGED")
    zone = ZoneInfo(config["storage_timezone"])
    boundary = datetime.combine(date.fromisoformat(config["cutoff_date"]), time(), zone)
    modified = datetime.fromisoformat(item.get("original_master_modified_at") or inventory["master_last_modified_at"])
    if modified.utcoffset() is None: raise ValueError("LOCAL_EXPORT_POLICY_INVALID")
    policy = (ZipPolicy.LEGACY_BEFORE_2026_03_04 if modified < boundary else ZipPolicy.CURRENT_ON_OR_AFTER_2026_03_04).value
    archives = []
    with stage_packaging_inputs(root, parts, report, expected_source_revision_hash=inventory["source_revision_hash"],
            policy=policy, workspace_root=workspace, operation_id=uuid4()) as staged:
        with assemble_packages(staged, workspace_root=workspace, storage_timezone=config["storage_timezone"]) as bundle:
            for archive in bundle.archives:
                digest = hashlib.sha256(); size = 0
                with bundle.open_archive(archive.filename) as descriptor, (output / archive.filename).open("xb") as target:
                    while chunk := os.read(descriptor, 1024**2):
                        target.write(chunk); digest.update(chunk); size += len(chunk)
                    target.flush(); os.fsync(target.fileno())
                if digest.hexdigest() != archive.sha256 or size != archive.size:
                    raise ValueError("LOCAL_EXPORT_ARCHIVE_CHANGED")
                archives.append({"name": archive.filename, "size": size, "sha256": digest.hexdigest()})
    return {"material_id": item["material_id"], "status": "COMPLETED", "issues": [], "archives": archives,
        "source_revision_hash": inventory["source_revision_hash"], "policy": policy,
        "source_entries": inventory["entries"],
        "master_last_modified_at": modified.isoformat(), "warnings": report["warnings"]}


def run(root, workspace, output, request):
    if (request.get("schema_version") != 1 or not 1 <= len(request["materials"]) <= 100
            or len({item["material_id"] for item in request["materials"]}) != len(request["materials"])):
        raise ValueError("LOCAL_EXPORT_REQUEST_INVALID")
    for item in request["materials"]:
        UUID(item["material_id"])
        if not re.fullmatch("[a-f0-9]{64}", item["metadata_sha256"]): raise ValueError("LOCAL_EXPORT_REQUEST_INVALID")
    items = [export_material(root, workspace, output, item, request["settings"]) for item in request["materials"]]
    return {"schema_version": 1, "status": "COMPLETED" if all(item["status"] == "COMPLETED" for item in items) else "FAILED", "items": items}


def main():
    output = Path("/output")
    workspace = Path("/tmp/publication-workspace"); workspace.mkdir(mode=0o700)
    try:
        request = json.loads(Path("/request/request.json").read_bytes())
        result = run(Path("/materials"), workspace, output, request)
    except Exception as error:
        code = str(error)
        result = {"schema_version": 1, "status": "FAILED", "items": [],
            "error_code": code if re.fullmatch("[A-Z][A-Z0-9_]{1,100}", code) else "LOCAL_EXPORT_PACKAGING_FAILED"}
    with (output / "result.json").open("x", encoding="utf-8") as target:
        json.dump(result, target, ensure_ascii=True, indent=2); target.flush(); os.fsync(target.fileno())


if __name__ == "__main__": main()
