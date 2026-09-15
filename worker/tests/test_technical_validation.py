import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.inventory import InventoryError
from app.secure_filesystem import secure_filesystem_access_supported
from app.technical_validation import probe_image, validate_material

POSIX = pytest.mark.skipif(not secure_filesystem_access_supported(), reason="secure Linux image probe required")
IDENTITY = "SAFE_0001_G03"


def test_busy_validation_is_rejected_without_queue_and_releases_after_failure(tmp_path, monkeypatch):
    from app.technical_validation import VALIDATION_SLOT
    with VALIDATION_SLOT:
        response = TestClient(create_app(tmp_path)).post("/internal/material-validate", json={"folder_path": IDENTITY})
        assert response.status_code == 503 and response.json()["detail"]["code"] == "VALIDATION_BUSY"
    def fail(*_): raise InventoryError("INVENTORY_SOURCE_CHANGED")
    monkeypatch.setattr("app.technical_validation._validate_material", fail)
    for _ in range(2):
        with pytest.raises(InventoryError, match="INVENTORY_SOURCE_CHANGED"):
            validate_material(tmp_path, (IDENTITY,))


def make(root: Path, *, size=(1024, 1024), maps=("COL", "NRM", "ROUGH")):
    from PIL import Image
    folder = root / IDENTITY
    (folder / "1K").mkdir(parents=True)
    for shortcut in maps:
        mode = "I;16" if shortcut.endswith("16") else "RGB"
        Image.new(mode, size).save(folder / "1K" / f"{IDENTITY}_{shortcut}_1K.png")
    return folder


def codes(result, section="errors"):
    return {finding["code"] for finding in result[section]}


@POSIX
def test_real_decoder_validates_maps_and_preserves_source_bytes(tmp_path):
    folder = make(tmp_path, maps=("COL", "NRM16", "DISP16", "ROUGH"))
    before = {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for p in folder.rglob("*.png")}
    result = validate_material(tmp_path, (IDENTITY,))
    assert result["can_approve"] is True and result["errors"] == []
    assert len(result["images"]) == 4
    assert next(image for image in result["images"] if image["map"] == "NRM16")["bits"] == 16
    assert codes(result, "warnings") == {"PREVIEW_MISSING", "SOURCE_METADATA_MISSING"}
    assert before == {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for p in folder.rglob("*.png")}
    assert str(tmp_path) not in json.dumps(result)


@POSIX
@pytest.mark.parametrize("defect,expected", [
    ("corrupt", "IMAGE_UNREADABLE"), ("truncated", "IMAGE_UNREADABLE"),
    ("extension", "MAP_EXTENSION_MISMATCH"), ("name", "MAP_FILENAME_INVALID"),
    ("shortcut", "MAP_SHORTCUT_UNSUPPORTED"), ("duplicate", "MAP_SHORTCUT_DUPLICATE"),
    ("wrong-16bit", "MAP_16BIT_REQUIRED"), ("dimensions", "MAP_DIMENSIONS_MISMATCH"),
    ("tiny", "MASTER_BELOW_1K"), ("nested", "MASTER_NESTED_DIRECTORY"),
    ("no-color", "COLOR_MAP_REQUIRED"),
])
def test_real_invalid_maps_cannot_be_approved(tmp_path, defect, expected):
    from PIL import Image
    folder = make(tmp_path)
    color = folder / "1K" / f"{IDENTITY}_COL_1K.png"
    if defect == "corrupt": color.write_bytes(b"PRIVATE_NOT_AN_IMAGE")
    elif defect == "truncated": color.write_bytes(color.read_bytes()[:45])
    elif defect == "extension": color.rename(color.with_suffix(".jpg"))
    elif defect == "name": color.rename(color.with_name("wrong-name.png"))
    elif defect == "shortcut": color.rename(color.with_name(f"{IDENTITY}_UNSUPPORTED_1K.png"))
    elif defect == "duplicate": Image.new("RGB", (1024, 1024)).save(color.with_suffix(".jpg"))
    elif defect == "wrong-16bit": (folder / "1K" / f"{IDENTITY}_NRM_1K.png").rename(folder / "1K" / f"{IDENTITY}_NRM16_1K.png")
    elif defect == "dimensions": Image.new("RGB", (1024, 512)).save(color)
    elif defect == "tiny": Image.new("RGB", (32, 32)).save(color)
    elif defect == "nested": (folder / "1K" / "nested").mkdir()
    else: color.unlink()
    result = validate_material(tmp_path, (IDENTITY,))
    assert result["can_approve"] is False and expected in codes(result)
    assert "PRIVATE_NOT_AN_IMAGE" not in json.dumps(result)


@POSIX
def test_absent_normal_surface_and_preview_are_explicit_warnings(tmp_path):
    make(tmp_path, maps=("COL",))
    result = validate_material(tmp_path, (IDENTITY,))
    assert result["can_approve"] is True
    assert {"NORMAL_MAP_MISSING", "SURFACE_RESPONSE_MAP_MISSING", "PREVIEW_MISSING"} <= codes(result, "warnings")


@POSIX
@pytest.mark.parametrize("extension,mode,expected_bits", [("jpg", "RGB", 8), ("webp", "RGB", 8),
                                                        ("tiff", "RGB", 8), ("tif", "I;16", 16)])
def test_supported_real_formats_are_decoded(tmp_path, extension, mode, expected_bits):
    from PIL import Image
    folder = make(tmp_path, maps=())
    Image.new(mode, (1024, 1024)).save(folder / "1K" / f"{IDENTITY}_COL_1K.{extension}")
    result = validate_material(tmp_path, (IDENTITY,))
    assert result["can_approve"] is True
    assert result["images"][0]["bits"] == expected_bits


@POSIX
@pytest.mark.parametrize("defect,expected", [("frames", "IMAGE_MULTIFRAME_UNSUPPORTED"),
                                           ("cmyk", "IMAGE_MODE_UNSUPPORTED"),
                                           ("float", "IMAGE_BIT_DEPTH_UNSUPPORTED")])
def test_unsupported_real_image_encodings_are_blocked(tmp_path, defect, expected):
    from PIL import Image
    folder = make(tmp_path, maps=())
    path = folder / "1K" / f"{IDENTITY}_COL_1K.tiff"
    if defect == "frames":
        Image.new("RGB", (1024, 1024)).save(path, save_all=True, append_images=[Image.new("RGB", (1024, 1024))])
    else:
        Image.new("CMYK" if defect == "cmyk" else "F", (1024, 1024)).save(path)
    result = validate_material(tmp_path, (IDENTITY,))
    assert result["can_approve"] is False and expected in codes(result)


@POSIX
@pytest.mark.parametrize("raw,code", [(b"{invalid private metadata", "SOURCE_METADATA_INVALID_FORMAT"),
                                     (b"texture size: 1.23456x2 cm", "SOURCE_METADATA_DIMENSION_UNSUPPORTED")])
def test_metadata_defects_are_reported_without_blocking_technical_review(tmp_path, raw, code):
    folder = make(tmp_path)
    (folder / "metadata.txt").write_bytes(raw)
    result = validate_material(tmp_path, (IDENTITY,))
    assert result["can_approve"] is True and code in codes(result, "warnings")
    assert raw.decode() not in json.dumps(result)


@POSIX
def test_content_replacement_during_probe_invalidates_whole_report(tmp_path, monkeypatch):
    folder = make(tmp_path)
    from app.technical_validation import probe_image as original
    changed = False
    def race(fd, **kwargs):
        nonlocal changed
        result = original(fd, **kwargs)
        if not changed:
            changed = True
            (folder / "extra.txt").write_bytes(b"concurrent source edit")
        return result
    monkeypatch.setattr("app.technical_validation.probe_image", race)
    with pytest.raises(InventoryError, match="INVENTORY_SOURCE_CHANGED"):
        validate_material(tmp_path, (IDENTITY,))


@POSIX
def test_api_exposes_only_verified_image_facts_and_source_hash(tmp_path):
    make(tmp_path)
    response = TestClient(create_app(tmp_path)).post("/internal/material-validate", json={"folder_path": IDENTITY})
    assert response.status_code == 200
    body = response.json()
    assert body["validator_version"] == "pbr-images-1" and body["can_approve"] is True
    assert {image["map"] for image in body["images"]} == {"COL", "NRM", "ROUGH"}
    assert "raw_content" not in response.text and str(tmp_path) not in response.text


@pytest.mark.parametrize("path", ["../outside", "/outside", "C:\\outside"])
def test_api_rejects_bad_path_without_probing(tmp_path, monkeypatch, path):
    monkeypatch.setattr("app.api.validate_material", lambda *_: pytest.fail("must not probe"))
    response = TestClient(create_app(tmp_path)).post("/internal/material-validate", json={"folder_path": path})
    assert response.status_code == 422


@pytest.mark.parametrize("failure", ["timeout", "nonzero", "oversized", "private-error", "bad-json", "partial", "oversized-image"])
def test_probe_process_is_bounded_and_cannot_reflect_child_diagnostics(monkeypatch, failure):
    def run(arguments, **kwargs):
        assert arguments[-1] == "42" and kwargs["pass_fds"] == (42,)
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert set(kwargs["env"]) == {"PATH", "PYTHONDONTWRITEBYTECODE"}
        if failure == "timeout": raise subprocess.TimeoutExpired(arguments, 1)
        body = {"width": 1024, "height": 1024, "bits": 8, "format": "PNG", "sha256": "a" * 64}
        if failure == "private-error": body = {"error": "PRIVATE_CHILD_DIAGNOSTIC"}
        elif failure == "partial": body = {"width": 1024}
        elif failure == "oversized-image": body["width"] = 9999999
        raw = json.dumps(body).encode()
        if failure == "oversized": raw = b"x" * 4097
        elif failure == "bad-json": raw = b"PRIVATE_INVALID_JSON"
        return SimpleNamespace(returncode=1 if failure == "nonzero" else 0, stdout=raw)
    monkeypatch.setattr(subprocess, "run", run)
    result = probe_image(42)
    assert result["error"] in {"IMAGE_PROBE_FAILED", "IMAGE_PROBE_TIMEOUT"}
    assert "PRIVATE" not in json.dumps(result)
