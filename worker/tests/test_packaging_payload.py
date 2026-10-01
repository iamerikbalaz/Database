"""Portable regressions for the retained export contract, without image conversion."""
from copy import deepcopy
from uuid import uuid4

import pytest

from app.packaging_plan import _digest
from app.packaging_store import PackagingStoreError, _validate_payload


def payload():
    operation = uuid4()
    plan_hash = "a" * 64
    archive = "SYNTHETIC_0001_G03_1K.zip"
    preview = "PREVIEW/detail views/Český náhled 2.png"
    bundle = {
        "operation_id": str(operation), "plan_sha256": plan_hash,
        "manifest_sha256": "b" * 64,
        "archives": [{"filename": archive, "size": 300, "sha256": "c" * 64, "entries": [
            {"path": archive[:-4] + "/PREVIEW/", "size": 0, "sha256": None},
            {"path": archive[:-4] + "/PREVIEW/detail views/", "size": 0, "sha256": None},
            {"path": archive[:-4] + "/" + preview, "size": 20, "sha256": "d" * 64},
        ]}],
    }
    value = {"schema_version": 1, "bundle": bundle, "bundle_sha256": _digest(bundle),
        "files": [
            {"path": "metadata.json", "size": 30, "sha256": "b" * 64},
            {"path": archive, "size": 300, "sha256": "c" * 64},
            {"path": preview, "size": 20, "sha256": "d" * 64},
        ], "directories": ["PREVIEW/", "PREVIEW/detail views/"]}
    return value, operation, plan_hash


def test_preview_manifest_accepts_archive_root_stripped_paths_and_preserves_input():
    value, operation, plan_hash = payload()
    before = deepcopy(value)
    assert _validate_payload(value, operation, plan_hash) == 350
    assert value == before


@pytest.mark.parametrize("defect", ["preview-hash", "preview-size", "preview-missing", "preview-extra", "parent-missing", "manifest-hash"])
def test_retained_preview_and_manifest_bytes_must_match_verified_bundle(defect):
    value, operation, plan_hash = payload()
    if defect == "preview-hash": value["files"][-1]["sha256"] = "e" * 64
    elif defect == "preview-size": value["files"][-1]["size"] += 1
    elif defect == "preview-missing": value["files"].pop()
    elif defect == "preview-extra": value["files"].append({"path": "PREVIEW/extra.png", "size": 1, "sha256": "f" * 64})
    elif defect == "parent-missing": value["directories"].pop()
    else: value["files"][0]["sha256"] = "f" * 64
    with pytest.raises(PackagingStoreError, match="CORRUPT_STATE"):
        _validate_payload(value, operation, plan_hash)
