import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.previews import PreviewError, list_previews, render_preview, _decode, PREVIEW_SLOTS
from app.secure_filesystem import secure_filesystem_access_supported

POSIX = pytest.mark.skipif(not secure_filesystem_access_supported(), reason="secure Linux image preview required")
IDENTITY = "SAFE_0001_G03"


@POSIX
@pytest.mark.parametrize("size", [256, 512, 1024])
def test_gallery_thumbnail_sizes_preserve_square_preview_and_original_bytes(tmp_path, size):
    from PIL import Image
    path = make(tmp_path, size=(1200, 1200))
    original = path.read_bytes(); before = path.stat().st_mtime_ns
    value = render_preview(tmp_path, (IDENTITY,), path.name, hashlib.sha256(original).hexdigest(), size)
    assert (value["width"], value["height"]) == (size, size)
    with Image.open(io.BytesIO(base64.b64decode(value["data"]))) as image:
        image.load(); assert image.size == (size, size)
    assert path.read_bytes() == original and path.stat().st_mtime_ns == before


@pytest.mark.parametrize("size", [0, 1200, 4096, True, "256"])
def test_bad_thumbnail_sizes_fail_before_any_source_access(monkeypatch, size):
    monkeypatch.setattr("app.previews._render_preview", lambda *_: pytest.fail("Unexpected source access"))
    with pytest.raises(PreviewError, match="PREVIEW_SIZE_UNSUPPORTED"):
        render_preview(Path("unused"), (IDENTITY,), "SPHERE_1.png", "a" * 64, size)


def make(root, extension="png", *, size=(1600, 800), mode="RGB"):
    from PIL import Image
    directory = root / IDENTITY / "PREVIEW"; directory.mkdir(parents=True)
    path = directory / ("Synthetic preview." + extension)
    Image.new(mode, size, 0).save(path)
    return path


def render(root, path):
    return render_preview(root, (IDENTITY,), path.name, hashlib.sha256(path.read_bytes()).hexdigest())


@POSIX
def test_actual_listing_and_decode_preserve_source_and_strip_private_metadata(tmp_path):
    from PIL import Image, PngImagePlugin
    path = make(tmp_path)
    metadata = PngImagePlugin.PngInfo(); metadata.add_text("Comment", "PRIVATE_SYNTHETIC_MARKER")
    exif = Image.Exif(); exif[270] = "PRIVATE_SYNTHETIC_EXIF"; exif[274] = 6
    Image.new("RGB", (1600, 800), (30, 70, 90)).save(path, pnginfo=metadata, exif=exif)
    original = (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ctime_ns)
    listing = list_previews(tmp_path, (IDENTITY,))
    assert listing == {"schema_version": 1, "folder_name": IDENTITY, "missing": False, "ignored_entries": 0,
        "items": [{"name": path.name, "size": path.stat().st_size, "sha256": hashlib.sha256(original[0]).hexdigest()}]}
    value = render(tmp_path, path)
    data = base64.b64decode(value["data"])
    assert value["width"] == 512 and value["height"] == 1024  # EXIF orientation applied
    assert value["sha256"] == hashlib.sha256(data).hexdigest()
    assert b"PRIVATE_SYNTHETIC" not in data
    assert str(tmp_path) not in json.dumps(value)
    with Image.open(io.BytesIO(data)) as image:
        image.load(); assert image.format == "JPEG" and not image.getexif()
        assert not any(key in image.info for key in ("comment", "exif", "icc_profile"))
    assert original == (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ctime_ns)


@POSIX
@pytest.mark.parametrize("extension,mode", [("jpg", "RGB"), ("jpeg", "RGB"), ("png", "RGBA"), ("webp", "RGB"), ("tif", "I;16"), ("tiff", "RGB")])
def test_all_supported_actual_images_render_without_upscaling(tmp_path, extension, mode):
    path = make(tmp_path, extension, size=(80, 40), mode=mode)
    value = render(tmp_path, path)
    assert value["width"] == 80 and value["height"] == 40


@POSIX
def test_missing_and_empty_preview_are_distinct_and_other_files_are_ignored(tmp_path):
    (tmp_path / IDENTITY).mkdir()
    assert list_previews(tmp_path, (IDENTITY,))["missing"] is True
    (tmp_path / IDENTITY / "PREVIEW").mkdir()
    assert list_previews(tmp_path, (IDENTITY,))["missing"] is False
    (tmp_path / IDENTITY / "PREVIEW" / "notes.txt").write_text("PRIVATE_SYNTHETIC_MARKER")
    (tmp_path / IDENTITY / "PREVIEW" / "png").write_text("PRIVATE_SYNTHETIC_MARKER")
    (tmp_path / IDENTITY / "PREVIEW" / "nested").mkdir()
    value = list_previews(tmp_path, (IDENTITY,))
    assert value["items"] == [] and value["ignored_entries"] == 3
    assert "PRIVATE_SYNTHETIC" not in json.dumps(value)


@POSIX
@pytest.mark.parametrize("kind", ["file-symlink", "directory-symlink", "hardlink", "fifo", "preview-symlink"])
def test_preview_links_and_special_files_are_rejected(tmp_path, kind):
    path = make(tmp_path)
    if kind == "file-symlink":
        other = tmp_path / "private.png"; path.rename(other); path.symlink_to(other)
    elif kind == "directory-symlink": (path.parent / "linked").symlink_to(tmp_path, target_is_directory=True)
    elif kind == "hardlink": os.link(path, tmp_path / "private-link.png")
    elif kind == "fifo": path.unlink(); os.mkfifo(path)
    else:
        folder = path.parent; other = tmp_path / "private-preview"; folder.rename(other); folder.symlink_to(other, target_is_directory=True)
    with pytest.raises(PreviewError, match="PREVIEW_UNSAFE_ENTRY"):
        list_previews(tmp_path, (IDENTITY,))


@pytest.mark.parametrize("name", ["../private.png", "/private.png", "C:\\private.png", "nested/image.png", "back\\slash.png", "x:stream.png", "x\x00.png", "x.svg", "x" * 256 + ".png"])
def test_invalid_selection_never_reaches_filesystem(tmp_path, monkeypatch, name):
    def forbidden(*_): raise AssertionError("Unsafe input reached filesystem")
    monkeypatch.setattr("app.previews._render_preview", forbidden)
    with pytest.raises(PreviewError, match="PREVIEW_UNSAFE_NAME"):
        render_preview(tmp_path, (IDENTITY,), name, "a" * 64)


@POSIX
@pytest.mark.parametrize("limit,code", [("MAX_ITEMS", "PREVIEW_ENTRY_LIMIT"), ("MAX_DIRECTORY_ENTRIES", "PREVIEW_ENTRY_LIMIT"),
    ("MAX_SOURCE_BYTES", "PREVIEW_FILE_LIMIT"), ("MAX_TOTAL_BYTES", "PREVIEW_TOTAL_LIMIT"), ("MAX_LIST_SECONDS", "PREVIEW_TIME_LIMIT")])
def test_listing_limits_fail_closed(tmp_path, monkeypatch, limit, code):
    make(tmp_path)
    monkeypatch.setattr("app.previews." + limit, -1 if limit == "MAX_LIST_SECONDS" else 0)
    with pytest.raises(PreviewError, match=code): list_previews(tmp_path, (IDENTITY,))


@POSIX
def test_stale_displayed_hash_and_missing_selection_fail_closed(tmp_path):
    path = make(tmp_path)
    with pytest.raises(PreviewError, match="PREVIEW_SOURCE_CHANGED"):
        render_preview(tmp_path, (IDENTITY,), path.name, "0" * 64)
    with pytest.raises(PreviewError, match="PREVIEW_NOT_FOUND"):
        render_preview(tmp_path, (IDENTITY,), "missing.png", "0" * 64)


@POSIX
def test_listing_rechecks_files_already_hashed_while_reading_later_files(tmp_path, monkeypatch):
    path = make(tmp_path); later = path.with_name("Z-last.png"); later.write_bytes(path.read_bytes())
    original = os.read
    def changing(fd, size):
        data = original(fd, size)
        if data and os.fstat(fd).st_ino == later.stat().st_ino:
            path.write_bytes(path.read_bytes() + b"changed")
        return data
    monkeypatch.setattr("app.previews.os.read", changing)
    with pytest.raises(PreviewError, match="PREVIEW_SOURCE_CHANGED"): list_previews(tmp_path, (IDENTITY,))


@POSIX
def test_actual_oversized_image_is_rejected_before_pixel_decode(tmp_path):
    path = make(tmp_path, size=(8192, 4097), mode="L")
    with pytest.raises(PreviewError, match="PREVIEW_PIXEL_LIMIT"): render(tmp_path, path)


@POSIX
def test_unsigned_16_bit_grayscale_preserves_midtones_in_display_preview(tmp_path):
    from PIL import Image
    path = make(tmp_path, size=(64, 64), mode="I;16")
    Image.new("I;16", (64, 64), 32768).save(path)
    value = render(tmp_path, path)
    with Image.open(io.BytesIO(base64.b64decode(value["data"]))) as display:
        assert all(126 <= channel <= 128 for channel in display.getpixel((32, 32)))


def test_decoder_receives_only_one_descriptor_and_a_scrubbed_environment(monkeypatch):
    def inspect_child(command, **options):
        assert command[-3:] == ["-m", "app.preview_decode", "3"]
        assert options["pass_fds"] == (3,) and options["timeout"] == 25
        assert options["env"] == {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}
        assert options["stdin"] == subprocess.DEVNULL and options["stderr"] == subprocess.DEVNULL
        return SimpleNamespace(stdout=b'{"error":"PREVIEW_UNREADABLE"}', returncode=1)
    monkeypatch.setattr("app.previews.subprocess.run", inspect_child)
    with pytest.raises(PreviewError, match="PREVIEW_UNREADABLE"): _decode(3)


@POSIX
@pytest.mark.parametrize("change", ["replace-file", "replace-directory", "modify-file", "replace-material"])
def test_decode_detects_actual_source_binding_changes(tmp_path, monkeypatch, change):
    path = make(tmp_path); original_decode = _decode
    def changing(fd):
        value = original_decode(fd)
        if change == "replace-file":
            path.rename(path.with_suffix(".old")); path.write_bytes(b"PRIVATE_SYNTHETIC_MARKER")
        elif change == "replace-directory":
            directory = path.parent; directory.rename(directory.with_name("old-preview")); directory.mkdir()
        elif change == "replace-material":
            material = path.parent.parent; material.rename(material.with_name("old-material")); material.mkdir()
        else: path.write_bytes(path.read_bytes() + b"changed")
        return value
    monkeypatch.setattr("app.previews._decode", changing)
    with pytest.raises(PreviewError, match="PREVIEW_SOURCE_CHANGED"):
        render(tmp_path, path)


@POSIX
@pytest.mark.parametrize("defect,code", [("corrupt", "PREVIEW_UNREADABLE"), ("truncated", "PREVIEW_UNREADABLE"),
    ("extension", "PREVIEW_EXTENSION_MISMATCH"), ("animated", "PREVIEW_MULTIFRAME_UNSUPPORTED")])
def test_actual_bad_images_return_only_safe_codes(tmp_path, defect, code):
    from PIL import Image
    path = make(tmp_path)
    if defect == "corrupt": path.write_bytes(b"PRIVATE_SYNTHETIC_MARKER")
    elif defect == "truncated": path.write_bytes(path.read_bytes()[:30])
    elif defect == "extension": other = path.with_suffix(".jpg"); path.rename(other); path = other
    else: Image.new("RGB", (32, 32), "red").save(path, save_all=True, append_images=[Image.new("RGB", (32, 32), "blue")])
    with pytest.raises(PreviewError, match=code): render(tmp_path, path)


def test_decoder_failure_diagnostics_do_not_escape(tmp_path, monkeypatch):
    def timeout(*args, **kwargs): raise subprocess.TimeoutExpired("PRIVATE_SYNTHETIC_MARKER", 1)
    monkeypatch.setattr("app.previews.subprocess.run", timeout)
    with pytest.raises(PreviewError, match="^PREVIEW_TIMEOUT$"): _decode(3)
    for content in (b"PRIVATE_SYNTHETIC_MARKER", b'{"error":"PRIVATE_SYNTHETIC_MARKER"}', b'{}', b'[]'):
        monkeypatch.setattr("app.previews.subprocess.run", lambda *_, **__: SimpleNamespace(stdout=content, returncode=0))
        with pytest.raises(PreviewError) as error: _decode(3)
        assert "PRIVATE_SYNTHETIC_MARKER" not in str(error.value)


def test_busy_preview_does_not_queue_and_slots_release_after_failure(tmp_path, monkeypatch):
    with PREVIEW_SLOTS, PREVIEW_SLOTS:
        response = TestClient(create_app(tmp_path)).post("/internal/material-previews", json={"folder_path": IDENTITY})
        assert response.status_code == 503 and response.json()["detail"]["code"] == "PREVIEW_BUSY"
    def failure(*_): raise PreviewError("PREVIEW_UNREADABLE")
    monkeypatch.setattr("app.previews._list_previews", failure)
    for _ in range(3):
        with pytest.raises(PreviewError, match="PREVIEW_UNREADABLE"): list_previews(tmp_path, (IDENTITY,))


@POSIX
def test_worker_preview_http_uses_safe_bounded_contract(tmp_path):
    path = make(tmp_path)
    client = TestClient(create_app(tmp_path))
    listing = client.post("/internal/material-previews", json={"folder_path": IDENTITY})
    assert listing.status_code == 200
    body = {"folder_path": IDENTITY, "name": path.name, "expected_sha256": listing.json()["items"][0]["sha256"]}
    response = client.post("/internal/material-preview", json=body)
    assert response.status_code == 200 and response.json()["media_type"] == "image/jpeg"
    assert client.post("/internal/material-preview", json={**body, "expected_sha256": "0" * 64}).status_code == 409
    assert client.post("/internal/material-preview", json={**body, "name": "../secret.png"}).status_code == 422
    assert str(tmp_path) not in response.text
