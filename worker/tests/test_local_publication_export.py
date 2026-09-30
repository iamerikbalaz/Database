from copy import deepcopy
import hashlib
import json
from uuid import uuid4
import zipfile

import pytest

from app.local_publication_export import run
from app.material_naming import base_name
from test_material_file_check import make_valid, add_map
from test_packaging_assembly import snapshot, runtime


def prepared(tmp_path):
    root, folder = make_valid(tmp_path, size=(2048, 1024), master="2K")
    workspace = tmp_path / "workspace"; workspace.mkdir(mode=0o700)
    source = (root, workspace, folder)
    document = (folder / "metadata.json").read_bytes()
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
    color = next((source[2] / "2K").glob("*_COL_2K.jpg")); color.write_bytes(b"not an image")
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


@pytest.mark.parametrize("sheen_mode", ["L", "RGB"])
def test_approved_optional_maps_and_sheen_survive_every_archive_and_web_manifest(tmp_path, sheen_mode):
    import io
    from PIL import Image
    source, output, request = prepared(tmp_path)
    for shortcut in ("DIFF", "METAL", "SPEC", "ID", "SPECLVL", "SSS", "SSSABSORB", "TRANSL", "ANISO", "SHEENGLOSS", "OPAC"):
        add_map(source[2], shortcut, size=(2048, 1024))
    add_map(source[2], "SHEEN", size=(2048, 1024), mode=sheen_mode)
    before = snapshot(source[2])
    result = run(source[0], source[1], output, request)
    assert result["status"] == "COMPLETED"
    assert len(result["items"][0]["source_entries"]) == len(before)
    expected = {"COL", "ROUGH", "NRM", "DIFF", "METAL", "SPEC", "ID", "SHEEN", "SPECLVL", "SSS", "SSSABSORB", "TRANSL", "ANISO", "SHEENGLOSS", "OPAC"}
    for item in result["items"][0]["archives"]:
        with zipfile.ZipFile(output / item["name"]) as archive:
            root = item["name"][:-4]
            manifest = json.loads(archive.read(root + "/metadata.json"))
            assert set(manifest["WEB_APP_PART"]["MAPS_SHORTCUTS"]) == expected
            resolution = root.rsplit("_", 1)[1]
            for shortcut in ("ID", "SHEEN"):
                image = archive.read(root + "/" + resolution + f"/{base_name(source[2].name)}_{shortcut}_{resolution}.png")
                assert image[24] == 8
                with Image.open(io.BytesIO(image)) as decoded:
                    decoded.load()
                    if shortcut == "SHEEN" and sheen_mode == "RGB":
                        red, green, blue = decoded.convert("RGB").getpixel((100, 100))
                        assert blue > green > red
            assert archive.testzip() is None
    assert snapshot(source[2]) == before


def test_export_enforces_identical_required_normal_and_preview_rules(tmp_path):
    source, output, request = prepared(tmp_path)
    next((source[2] / "2K").glob("*_NRM_2K.jpg")).unlink()
    add_map(source[2], "NRM16", size=(2048, 1024))
    from PIL import Image
    Image.new("RGB", (64, 64)).save(source[2] / "PREVIEW" / "SPHERE_1.png")
    result = run(source[0], source[1], output, request)
    assert result["status"] == "FAILED"
    assert {item["code"] for item in result["items"][0]["issues"]} >= {"REQUIRED_MAP_MISSING", "PREVIEW_DIMENSIONS_INVALID"}
    assert list(output.iterdir()) == []


@pytest.mark.parametrize("extension,mode", [("tif", "L"), ("tiff", "RGB")])
def test_export_accepts_thousands_bucket_tiff_color_and_jpeg_id_preserving_source(tmp_path, extension, mode):
    from PIL import Image
    import io
    source, output, request = prepared(tmp_path)
    # A non-1024-multiple rectangular 8K master is a valid source. Export
    # continues to normalize the generated output using its existing policy.
    (source[2] / "2K").rename(source[2] / "8K")
    for old in (source[2] / "8K").iterdir(): old.unlink()
    for shortcut in ("COL", "ROUGH", "NRM"):
        add_map(source[2], shortcut, size=(8600, 256),
            **({"fmt": "TIFF", "extension": extension} if shortcut == "COL" else {}))
    add_map(source[2], "ID", size=(8600, 256), fmt="JPEG", mode=mode)
    before = snapshot(source[2])
    result = run(source[0], source[1], output, request)
    assert result["status"] == "COMPLETED"
    assert any(item["code"] == "COL_TIFF_LEGACY" for item in result["items"][0]["warnings"])
    assert len(result["items"][0]["archives"]) == 4
    for item in result["items"][0]["archives"]:
        with zipfile.ZipFile(output / item["name"]) as archive:
            assert archive.testzip() is None
            root = item["name"][:-4]
            resolution = root.rsplit("_", 1)[1]
            for shortcut, suffix, fmt in (("COL", extension, "TIFF"), ("ID", "jpg", "JPEG")):
                raw = archive.read(f"{root}/{resolution}/{base_name(source[2].name)}_{shortcut}_{resolution}.{suffix}")
                with Image.open(io.BytesIO(raw)) as image:
                    image.load()
                    assert image.format == fmt and image.width == int(resolution[:-1]) * 1024
    assert snapshot(source[2]) == before
    assert list(source[1].iterdir()) == []
