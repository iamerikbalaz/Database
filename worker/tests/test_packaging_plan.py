from copy import deepcopy
import hashlib
import json

import pytest

from app.packaging_plan import PackagingPlanError, build_packaging_plan
from app.preflight import ZipPolicy

IDENTITY = "SYNTHETIC_LONG_PREFIX_0001_G03"


def rehash(report):
    inventory = report["inventory"]
    inventory["entries"].sort(key=lambda entry: entry["path"])
    inventory["total_bytes"] = sum(entry["size"] for entry in inventory["entries"])
    value = {key: inventory[key] for key in ("schema_version", "folder_name", "master_resolution", "policy", "entries")}
    inventory["source_revision_hash"] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    return report


def report(width=4096, height=4096, master="4K", *, metadata=True, previews=True):
    value = {"schema_version": 1, "validator_version": "pbr-images-1", "can_approve": True, "errors": [], "warnings": [],
        "inventory": {"schema_version": 1, "folder_name": IDENTITY, "master_resolution": master,
            "policy": ZipPolicy.CURRENT_ON_OR_AFTER_2026_03_04.value, "entries": [{"path": master, "kind": "directory", "size": 0, "sha256": None}]}, "images": []}
    entries = value["inventory"]["entries"]
    for shortcut, bits, extension in (("COL", 8, "png"), ("NRM16", 16, "tif"), ("ROUGH", 8, "jpg")):
        path = f"{master}/{IDENTITY}_{shortcut}_{master}.{extension}"; digest = hashlib.sha256(path.encode()).hexdigest()
        entries.append({"path": path, "kind": "file", "size": 1024, "sha256": digest})
        value["images"].append({"path": path, "map": shortcut, "width": width, "height": height, "bits": bits,
            "format": {"png": "PNG", "tif": "TIFF", "jpg": "JPEG"}[extension], "sha256": digest})
    if metadata: entries.append({"path": "metadata.txt", "kind": "file", "size": 30, "sha256": "a" * 64})
    if previews: entries.extend([{"path": "PREVIEW", "kind": "directory", "size": 0, "sha256": None},
        {"path": "PREVIEW/view.png", "kind": "file", "size": 1024, "sha256": "b" * 64}])
    return rehash(value)


def plan(value=None, policy=ZipPolicy.CURRENT_ON_OR_AFTER_2026_03_04.value):
    value = value or report()
    return build_packaging_plan(value, expected_source_revision_hash=value["inventory"]["source_revision_hash"], policy=policy)


def test_pure_plan_preserves_inputs_and_complete_archive_layout():
    value = report(); before = deepcopy(value); result = plan(value)
    assert value == before and result == plan(value) and len(result.sha256) == 64
    assert [item.name for item in result.resolutions] == ["4K", "2K", "1K"]
    assert all(item.action == "COPY" and not item.input_is_effective_master for item in result.resolutions[0].maps)
    assert all(item.action == "RESIZE" and item.input_is_effective_master for item in result.resolutions[1].maps)
    assert result.production_metadata.path == "metadata.txt" and result.production_metadata.sha256 == "a" * 64
    for target in result.resolutions:
        assert target.archive_name == f"{IDENTITY}_{target.name}.zip" and target.archive_root == f"{IDENTITY}_{target.name}"
        assert "metadata.json" in target.archive_entries and f"{target.name}/metadata.txt" in target.archive_entries
        assert "PREVIEW/view.png" in target.archive_entries
        assert not any("SOURCE" in item for item in target.archive_entries)
    manifest = json.loads(result.web_manifest())
    assert set(manifest) == {"WEB_APP_PART", "DESKTOP_APP_PART"}
    assert manifest["DESKTOP_APP_PART"] == {}
    assert manifest["WEB_APP_PART"]["MAPS_SHORTCUTS"] == ["COL", "NRM16", "ROUGH"]
    assert manifest["WEB_APP_PART"]["TEXTURE_RESOLUTIONS"] == {"4K": "4096x4096", "2K": "2048x2048", "1K": "1024x1024"}


@pytest.mark.parametrize("width,height,master,effective,dimensions", [
    (16384, 5890, "16K", "16K", [(16384, 5890), (8192, 2945), (4096, 1473), (2048, 736), (1024, 368)]),
    (5890, 16384, "16K", "16K", [(5890, 16384), (2945, 8192), (1473, 4096), (736, 2048), (368, 1024)]),
    (6144, 3072, "8K", "6K", [(6144, 3072), (4096, 2048), (2048, 1024), (1024, 512)]),
    (3500, 1750, "4K", "3K", [(3072, 1536), (2048, 1024), (1024, 512)]),
    (4096, 2048, "2K", "2K", [(2048, 1024), (1024, 512)]),
    (1024, 1024, "1K", "1K", [(1024, 1024)]),
])
def test_resolution_decisions_match_archived_script_geometry(width, height, master, effective, dimensions):
    result = plan(report(width, height, master))
    assert result.effective_master == effective
    assert [(item.width, item.height) for item in result.resolutions] == dimensions
    if width != height: assert all(item.action == "RESIZE" for item in result.resolutions[0].maps)
    if width == 16384: assert json.loads(result.web_manifest())["WEB_APP_PART"]["IMAGE_RATIO"] == 0.359497


def test_policy_is_explicit_and_changes_plan_without_changing_source_inventory():
    current = plan(); legacy = plan(policy=ZipPolicy.LEGACY_BEFORE_2026_03_04.value)
    assert current.source_revision_hash == legacy.source_revision_hash and current.sha256 != legacy.sha256
    with pytest.raises(PackagingPlanError, match="POLICY_REQUIRED"): plan(policy="automatic")


def test_optional_metadata_and_preview_absence_are_explicit_warnings():
    result = plan(report(metadata=False, previews=False))
    assert result.warnings == ("PRODUCTION_METADATA_MISSING", "PREVIEW_MISSING")
    assert result.production_metadata is None and result.previews == ()
    assert not any("metadata.txt" in path for item in result.resolutions for path in item.archive_entries)


def test_matching_existing_nested_metadata_is_copied_once():
    value = report(); value["inventory"]["entries"].append({"path": "4K/metadata.txt", "kind": "file", "size": 30, "sha256": "a" * 64})
    result = plan(rehash(value))
    assert result.resolutions[0].archive_entries.count("4K/metadata.txt") == 1


@pytest.mark.parametrize("defect", ["digest", "blocked", "version", "identity", "zero", "traversal", "case-collision", "parent", "total", "file-kind",
    "map-digest", "map-path", "map-format", "map-bits", "duplicate-map", "dimensions", "small", "large", "metadata-collision", "unreviewed", "missing-color"])
def test_refuses_untrusted_or_inconsistent_inputs_without_reflecting_them(defect):
    value = report(); inventory = value["inventory"]; images = value["images"]
    if defect == "digest": inventory["source_revision_hash"] = "f" * 64
    elif defect == "blocked": value["can_approve"] = False
    elif defect == "version": value["validator_version"] = "unverified"
    elif defect == "identity": inventory["folder_name"] = "SECRET/UNSAFE"
    elif defect == "zero": inventory["folder_name"] = "SYNTHETIC_0000_G03"
    elif defect == "traversal": inventory["entries"][0]["path"] = "../SECRET"
    elif defect == "case-collision": inventory["entries"].append({**inventory["entries"][-1], "path": inventory["entries"][-1]["path"].upper()})
    elif defect == "parent": inventory["entries"].append({"path": "missing/SECRET", "kind": "file", "size": 1, "sha256": "c" * 64})
    elif defect == "total": inventory["total_bytes"] += 1
    elif defect == "file-kind": inventory["entries"][1]["kind"] = "symlink"
    elif defect == "map-digest": images[0]["sha256"] = "f" * 64
    elif defect == "map-path": images[0]["path"] = "../SECRET.png"
    elif defect == "map-format": images[0]["format"] = "TIFF"
    elif defect == "map-bits": images[1]["bits"] = 8
    elif defect == "duplicate-map": images.append(images[0])
    elif defect == "dimensions": images[1]["height"] += 1
    elif defect == "small": value = report(100, 100)
    elif defect == "large": value = report(40000, 40000)
    elif defect == "metadata-collision": inventory["entries"].append({"path": "4K/metadata.txt", "kind": "file", "size": 30, "sha256": "c" * 64})
    elif defect == "unreviewed": inventory["entries"].append({"path": "4K/SECRET.png", "kind": "file", "size": 1, "sha256": "c" * 64})
    elif defect == "missing-color": images[:] = images[1:]
    if defect not in {"digest", "total"}: rehash(value)
    with pytest.raises(PackagingPlanError) as error: plan(value)
    assert "SECRET" not in str(error.value)
