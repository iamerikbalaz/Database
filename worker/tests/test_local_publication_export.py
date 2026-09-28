from copy import deepcopy
import hashlib
import json
from uuid import uuid4
import zipfile

import pytest

from app.local_publication_export import run
from app.technical_validation import validate_material
from test_packaging_assembly import make, snapshot, runtime


def prepared(tmp_path):
    source = make(tmp_path, size=(2048, 1024), master="2K")
    (source[2] / "metadata.txt").unlink()
    document = b'{"COLOR":{"hex":"#FFFFFF"},"TEXTURE_SIZE":{"cm":{"width":10,"height":20}}}'
    (source[2] / "metadata.json").write_bytes(document)
    output = tmp_path / "output"; output.mkdir()
    request = {"schema_version": 1, "materials": [{"material_id": str(uuid4()), "folder_path": source[2].name,
        "identity_name": source[2].name, "metadata_sha256": hashlib.sha256(document).hexdigest()}],
        "settings": {"cutoff_date": "2026-03-04", "storage_timezone": "Europe/Prague"}}
    return source, output, request


@pytest.mark.parametrize("original,expected", [("2026-03-03T23:59:59+01:00", "LEGACY_BEFORE_2026_03_04"),
    ("2026-03-04T00:00:00+01:00", "CURRENT_ON_OR_AFTER_2026_03_04")])
def test_offline_export_uses_original_date_global_rule_and_verified_complete_zip_contents(tmp_path, original, expected):
    source, output, request = prepared(tmp_path)
    request["materials"][0]["original_master_modified_at"] = original
    before = snapshot(source[2]); frozen = deepcopy(request)
    result = run(source[0], source[1], output, request)
    assert result["status"] == "COMPLETED" and request == frozen
    assert result["items"][0]["policy"] == expected
    assert len(result["items"][0]["archives"]) == 2
    for item in result["items"][0]["archives"]:
        raw = (output / item["name"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == item["sha256"] and len(raw) == item["size"]
        with zipfile.ZipFile(output / item["name"]) as archive:
            assert archive.testzip() is None
            assert not any("SOURCE/" in name for name in archive.namelist())
            if expected.startswith("LEGACY"): assert all(item.date_time == (2026, 1, 1, 0, 0, 0) for item in archive.infolist())
    assert snapshot(source[2]) == before
    assert list(source[1].iterdir()) == []


def test_changed_source_metadata_blocks_packaging_without_output(tmp_path):
    source, output, request = prepared(tmp_path)
    request["materials"][0]["metadata_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="METADATA_CHANGED"): run(source[0], source[1], output, request)
    assert list(output.iterdir()) == []


def test_bad_maps_return_material_scoped_issues_without_zip(tmp_path):
    source, output, request = prepared(tmp_path)
    color = next((source[2] / "2K").glob("*_COL_2K.png")); color.write_bytes(b"not an image")
    result = run(source[0], source[1], output, request)
    assert result["status"] == "FAILED" and result["items"][0]["issues"]
    assert result["items"][0]["material_id"] == request["materials"][0]["material_id"]
    assert list(output.iterdir()) == []


def test_global_cutoff_revision_changes_method_without_modifying_source(tmp_path):
    source, output, request = prepared(tmp_path)
    request["materials"][0]["original_master_modified_at"] = "2026-03-15T00:00:00+01:00"
    request["settings"]["cutoff_date"] = "2026-04-01"
    result = run(source[0], source[1], output, request)
    assert result["items"][0]["policy"] == "LEGACY_BEFORE_2026_03_04"


def test_all_five_historical_map_types_survive_every_archive_and_web_manifest(tmp_path):
    from PIL import Image
    source, output, request = prepared(tmp_path)
    for shortcut in ("DIFF", "METAL", "SPEC", "ID", "MASK"):
        Image.new("RGB", (2048, 1024), (91, 126, 211)).save(source[2] / "2K" / f"{source[2].name}_{shortcut}_2K.png")
    before = snapshot(source[2])
    result = run(source[0], source[1], output, request)
    assert result["status"] == "COMPLETED"
    assert len(result["items"][0]["source_entries"]) == len(before)
    expected = {"COL", "NRM16", "DIFF", "METAL", "SPEC", "ID", "MASK"}
    for item in result["items"][0]["archives"]:
        with zipfile.ZipFile(output / item["name"]) as archive:
            root = item["name"][:-4]
            manifest = json.loads(archive.read(root + "/metadata.json"))
            assert set(manifest["WEB_APP_PART"]["MAPS_SHORTCUTS"]) == expected
            resolution = root.rsplit("_", 1)[1]
            for shortcut in ("DIFF", "METAL", "SPEC", "ID", "MASK"):
                image = archive.read(root + "/" + resolution + f"/{source[2].name}_{shortcut}_{resolution}.png")
                assert image[24] == 8
            assert archive.testzip() is None
    assert snapshot(source[2]) == before
