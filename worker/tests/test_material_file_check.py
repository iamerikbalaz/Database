import hashlib
import json
import os
from pathlib import Path
import struct
from uuid import uuid4
import zlib

from PIL import Image
import pytest

from app.inventory import InventoryError
from app.local_file_check import run
from app.material_file_check import MAP_RULES, PROFILE, check_material_files
from app.material_naming import base_name
from app.technical_validation import validate_material


pytestmark = pytest.mark.skipif(os.name != "posix", reason="Secure descriptor-relative source checks require Linux")
IDENTITY = "ROUBAL_0001_TILES-ORANGE_B01"


def png16_rgb(path, size, *, alpha=False):
    channels = 4 if alpha else 3
    pixel = bytes.fromhex("8080ffff0101ffff" if alpha else "8080ffff0101")
    row = b"\0" + pixel * size[0]
    def chunk(name, data):
        return struct.pack("!I", len(data)) + name + data + struct.pack("!I", zlib.crc32(name + data) & 0xffffffff)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", *size, 16, 6 if alpha else 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * size[1])) + chunk(b"IEND", b""))


def add_map(folder, shortcut, *, size=(1024, 512), fmt=None, mode=None, extension=None, bits=None):
    expected_fmt, expected_bits, modes = MAP_RULES[shortcut]
    fmt = fmt or expected_fmt
    mode = mode or ("I;16" if shortcut == "DISP16" else "RGB" if "RGB" in modes else "L")
    extension = extension or {"JPEG": "jpg", "PNG": "png", "TIFF": "tif"}[fmt]
    master = next(path.name for path in folder.iterdir() if path.is_dir() and path.name.endswith("K"))
    path = folder / master / f"{base_name(folder.name)}_{shortcut}_{master}.{extension}"
    if shortcut == "NRM16" and (bits or expected_bits) == 16 and fmt == "PNG" and mode in {"RGB", "RGBA"}:
        png16_rgb(path, size, alpha=mode == "RGBA")
    else:
        color = (41, 129, 231) if mode == "RGB" else (41, 129, 231, 255) if mode == "RGBA" else 128
        Image.new(mode, size, color).save(path, format=fmt)
    return path


def make_valid(tmp_path, *, size=(1024, 512), master="1K", identity=IDENTITY):
    root = tmp_path / "materials"; root.mkdir()
    folder = root / identity; (folder / master).mkdir(parents=True)
    for shortcut in ("COL", "ROUGH", "NRM"): add_map(folder, shortcut, size=size)
    (folder / "PREVIEW").mkdir()
    Image.new("RGB", (1200, 1200), (80, 100, 160)).save(folder / "PREVIEW" / "SPHERE_1.png")
    (folder / "metadata.json").write_text(json.dumps({"COLOR": {"hex": "#445566"}, "TEXTURE_SIZE": {"cm": {"width": 10, "height": 20}}}))
    return root, folder


def checked(root, folder): return check_material_files(root, (folder.name,))


def codes(result): return {item["code"] for item in result["findings"]}


def digest_tree(folder):
    return {path.relative_to(folder).as_posix(): (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in folder.rglob("*") if path.is_file()}


def test_complete_rectangular_master_readonly_and_legacy_contract_unchanged(tmp_path):
    root, folder = make_valid(tmp_path)
    before = digest_tree(folder)
    value = checked(root, folder)
    assert value["profile"] == PROFILE and value["complete"] is True and value["issues"] == []
    assert "Status: OK" in value["report"] and IDENTITY in value["report"]
    assert all("mode" not in image for image in value["packaging_report"]["images"])
    assert value["packaging_report"] == validate_material(root, (folder.name,))
    assert digest_tree(folder) == before


@pytest.mark.parametrize("shortcut", ["COL", "ROUGH", "NRM"])
def test_required_maps_cannot_be_replaced_by_optional_variants(tmp_path, shortcut):
    root, folder = make_valid(tmp_path)
    next((folder / "1K").glob(f"*_{shortcut}_1K.jpg")).unlink()
    add_map(folder, "NRM16"); add_map(folder, "GLOSS")
    result = checked(root, folder)
    assert "REQUIRED_MAP_MISSING" in codes(result)
    assert any(f"readable {shortcut} map" in issue for issue in result["issues"])


def test_all_three_missing_required_maps_are_listed(tmp_path):
    root, folder = make_valid(tmp_path)
    for path in (folder / "1K").iterdir(): path.unlink()
    result = checked(root, folder)
    for shortcut in ("COL", "ROUGH", "NRM"):
        assert any(f"readable {shortcut} map" in issue for issue in result["issues"])


@pytest.mark.parametrize("shortcut", sorted(MAP_RULES))
def test_each_approved_map_format_is_accepted(tmp_path, shortcut):
    root, folder = make_valid(tmp_path)
    add_map(folder, shortcut)
    assert checked(root, folder)["issues"] == []


@pytest.mark.parametrize("shortcut,mode", [("SHEEN", "L"), ("SHEEN", "RGB"), ("NRM16", "RGBA")])
def test_sheen_gray_and_rgb_and_normal16_alpha_are_supported(tmp_path, shortcut, mode):
    root, folder = make_valid(tmp_path)
    add_map(folder, shortcut, mode=mode)
    assert checked(root, folder)["issues"] == []


@pytest.mark.parametrize("shortcut,fmt,mode,bits", [
    ("ID", "PNG", "RGB", 8), ("ID", "PNG", "LA", 8), ("ID", "PNG", "1", 1),
    ("SHEEN", "PNG", "RGBA", 8), ("SHEEN", "JPEG", "RGB", 8), ("SHEEN", "PNG", "I;16", 16),
    ("NRM16", "PNG", "L", 8), ("NRM16", "PNG", "RGB", 8), ("DISP16", "TIFF", "L", 8),
    ("COL", "PNG", "RGB", 8), ("ROUGH", "JPEG", "RGB", 8), ("NRM", "JPEG", "L", 8),
])
def test_wrong_channels_formats_and_stored_depth_are_rejected(tmp_path, shortcut, fmt, mode, bits):
    root, folder = make_valid(tmp_path)
    for existing in (folder / "1K").glob(f"*_{shortcut}_1K.*"): existing.unlink()
    path = add_map(folder, shortcut, fmt=fmt, mode=mode, bits=bits)
    result = checked(root, folder)
    assert "MAP_PIXEL_FORMAT_INVALID" in codes(result)
    assert any(path.name in issue and "actual" in issue for issue in result["issues"])


def test_png_transparency_key_is_an_alpha_channel_for_strict_checks(tmp_path):
    root, folder = make_valid(tmp_path)
    path = add_map(folder, "ID")
    Image.new("L", (1024, 512), 128).save(path, transparency=0)
    assert "MAP_PIXEL_FORMAT_INVALID" in codes(checked(root, folder))


def test_mask_does_not_silently_alias_id(tmp_path):
    root, folder = make_valid(tmp_path)
    path = add_map(folder, "ID")
    path.rename(path.with_name(path.name.replace("_ID_", "_MASK_")))
    result = checked(root, folder)
    assert "MAP_SHORTCUT_UNSUPPORTED" in codes(result)
    assert any("expected ID" in issue for issue in result["issues"])


@pytest.mark.parametrize("change,expected", [
    ("additional-resolution", "SINGLE_MASTER_REQUIRED"), ("wrong-exact-resolution", "MASTER_DIMENSIONS_DIFFER"),
    ("different-size", "MAP_DIMENSIONS_MISMATCH"), ("wrong-prefix", "MAP_FILENAME_INVALID"),
    ("lowercase-identity", "MATERIAL_FOLDER_NAME_INVALID"), ("duplicate-map", "MAP_SHORTCUT_DUPLICATE"),
    ("wrong-extension", "MAP_EXTENSION_MISMATCH"), ("broken-image", "IMAGE_UNREADABLE"),
    ("wrong-metadata", "HEX_COLOR_INVALID"), ("missing-metadata", "SOURCE_METADATA_MISSING"),
])
def test_source_structure_and_content_defects_are_reported(tmp_path, change, expected):
    root, folder = make_valid(tmp_path)
    color = next((folder / "1K").glob("*_COL_*.jpg"))
    if change == "additional-resolution": (folder / "2K").mkdir()
    elif change == "wrong-exact-resolution": add_map(folder, "COL", size=(1100, 512))
    elif change == "different-size": add_map(folder, "ROUGH", size=(1024, 500))
    elif change == "wrong-prefix": color.rename(color.with_name(color.name.replace("ROUBAL", "OTHER")))
    elif change == "lowercase-identity":
        target = folder.with_name(folder.name.lower()); folder.rename(target); folder = target
    elif change == "duplicate-map": color.with_suffix(".jpeg").write_bytes(color.read_bytes())
    elif change == "wrong-extension": color.rename(color.with_suffix(".png"))
    elif change == "broken-image": color.write_bytes(b"not an image")
    elif change == "wrong-metadata": (folder / "metadata.json").write_text('{"COLOR":{"hex":"wrong"}}')
    elif change == "missing-metadata": (folder / "metadata.json").unlink()
    result = checked(root, folder)
    assert result["complete"] is True and expected in codes(result)


@pytest.mark.parametrize("change,expected", [
    ("fake-main", "PRIMARY_PREVIEW_REQUIRED"), ("wrong-size", "PREVIEW_DIMENSIONS_INVALID"),
    ("fake-png", "PREVIEW_FORMAT_INVALID"), ("broken", "IMAGE_UNREADABLE"),
    ("extra-jpg", "PREVIEW_FORMAT_INVALID"),
])
def test_all_previews_are_decoded_with_exact_primary_name(tmp_path, change, expected):
    root, folder = make_valid(tmp_path)
    path = folder / "PREVIEW" / "SPHERE_1.png"
    if change == "fake-main": path.rename(path.with_name("SPHERE_10.png"))
    elif change == "wrong-size": Image.new("RGB", (1199, 1200)).save(path)
    elif change == "fake-png": Image.new("RGB", (1200, 1200)).save(path, format="JPEG")
    elif change == "broken": path.write_bytes(b"invalid png")
    elif change == "extra-jpg": Image.new("RGB", (1200, 1200)).save(path.with_name("CLOSEUP_2.jpg"))
    assert expected in codes(checked(root, folder))


def test_fabric_primary_is_accepted(tmp_path):
    root, folder = make_valid(tmp_path)
    (folder / "PREVIEW" / "SPHERE_1.png").rename(folder / "PREVIEW" / "FABRIC_1.png")
    assert checked(root, folder)["issues"] == []


def test_legacy_metadata_txt_cannot_certify_missing_canonical_json(tmp_path):
    root, folder = make_valid(tmp_path)
    (folder / "metadata.json").rename(folder / "metadata.txt")
    assert "SOURCE_METADATA_JSON_REQUIRED" in codes(checked(root, folder))


@pytest.mark.parametrize("probe_error", ["IMAGE_PROBE_UNAVAILABLE", "IMAGE_PROBE_FAILED", "IMAGE_PROBE_TIMEOUT", "IMAGE_SOURCE_CHANGED"])
@pytest.mark.parametrize("target", ["app.technical_validation.probe_image", "app.material_file_check.probe_image"])
def test_probe_infrastructure_errors_and_races_abort_the_check(tmp_path, monkeypatch, probe_error, target):
    root, folder = make_valid(tmp_path)
    monkeypatch.setattr(target, lambda *_args, **_kwargs: {"error": probe_error})
    with pytest.raises(InventoryError, match="FILE_CHECK_INCOMPLETE"): checked(root, folder)


def test_source_change_after_preview_probe_is_never_certified(tmp_path, monkeypatch):
    from app import material_file_check as checker
    root, folder = make_valid(tmp_path)
    original = checker.probe_image
    def modified(*args, **kwargs):
        result = original(*args, **kwargs)
        (folder / "changed.txt").write_text("concurrent modification")
        return result
    monkeypatch.setattr(checker, "probe_image", modified)
    with pytest.raises(InventoryError, match="SOURCE_CHANGED"): checked(root, folder)


def test_symlink_is_not_followed(tmp_path):
    root, folder = make_valid(tmp_path)
    os.symlink(tmp_path / "outside", folder / "link")
    with pytest.raises(InventoryError, match="UNSAFE_ENTRY"): checked(root, folder)


def test_cli_wire_shape_contains_only_full_results(tmp_path):
    root, folder = make_valid(tmp_path); identifier = str(uuid4())
    result = run(root, {"schema_version": 1, "materials": [{"id": identifier, "folder_path": folder.name}]})
    assert set(result) == {"schema_version", "results"}
    assert set(result["results"][0]) == {"id", "profile", "complete", "issues", "report"}
    assert result["results"][0]["id"] == identifier and result["results"][0]["issues"] == []
    with pytest.raises(ValueError):
        run(root, {"schema_version": 1, "materials": [{"id": identifier, "folder_path": "../outside"}]})
