from array import array
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys

from PIL import Image
import pytest

from app import packaging_convert as conversion
from app.packaging_convert import PackagingConversionError, convert_map
from app.packaging_plan import MapOperation


@pytest.fixture(autouse=True)
def required_runtime():
    if not Path(conversion.EXECUTABLE).is_file():
        if os.environ.get("REQUIRE_PACKAGING_RUNTIME") == "1": pytest.fail("Required packaging runtime is absent")
        pytest.skip("Opt-in Linux ImageMagick Q16-HDRI runtime required")


@contextmanager
def image_files(tmp_path, image_format="PNG", bits=8, width=1024, height=368):
    root = tmp_path / "cache"; root.mkdir(mode=0o700)
    source = tmp_path / "input"; target = tmp_path / "output"
    if bits == 16:
        pixels = array("H", ((x * 61 + y * 3) % 65536 for y in range(height) for x in range(width)))
        Image.frombytes("I;16", (width, height), pixels.tobytes()).save(source, format=image_format)
    else:
        image = Image.new("RGB", (width, height))
        image.putdata([((x * 13) % 256, (y * 17) % 256, (x + y) % 256) for y in range(height) for x in range(width)])
        image.save(source, format=image_format)
    source.chmod(0o400); digest = hashlib.sha256(source.read_bytes()).hexdigest()
    operation = MapOperation("2K/SYNTHETIC_0001_G01_COL_2K.png", "1K/SYNTHETIC_0001_G01_COL_1K.png", digest,
        "COL", image_format, bits, 512, 184, "RESIZE", False)
    source_fd = os.open(source, os.O_RDONLY)
    target_fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try: yield source_fd, target_fd, operation, root, source, target
    finally: os.close(source_fd); os.close(target_fd)


def run(files, **options):
    source, target, operation, cache, _, _ = files
    return convert_map(source, target, operation, expected_input_sha256=operation.source_sha256, cache_root=cache, **options)


@pytest.mark.parametrize("image_format,bits", [("PNG", 8), ("PNG", 16), ("TIFF", 16), ("TIFF", 8), ("JPEG", 8), ("WEBP", 8)])
def test_actual_conversion_preserves_geometry_depth_source_and_verifiable_bytes(tmp_path, image_format, bits):
    with image_files(tmp_path, image_format, bits) as files:
        before = files[4].read_bytes(); original = files[4].stat()
        proof = run(files)
        assert (proof.width, proof.height, proof.format, proof.bits) == (512, 184, image_format, bits)
        assert proof.sha256 == hashlib.sha256(files[5].read_bytes()).hexdigest() and proof.size == files[5].stat().st_size
        assert proof.input_sha256 == files[2].source_sha256 and proof.runtime_version.startswith("7.") and len(proof.policy_sha256) == 64
        assert files[4].read_bytes() == before and files[4].stat().st_mtime_ns == original.st_mtime_ns
        assert stat.S_IMODE(files[5].stat().st_mode) == 0o400 and list(files[3].iterdir()) == []
        if bits == 16:
            with Image.open(files[5]) as converted:
                assert len(set(converted.get_flattened_data())) > 256  # Cannot pass after 8-bit quantization.


def test_real_runtime_ignores_inherited_imagemagick_proxy_and_python_settings(tmp_path, monkeypatch):
    for key in ("MAGICK_CONFIGURE_PATH", "MAGICK_CODER_MODULE_PATH", "MAGICK_TEMPORARY_PATH", "PYTHONPATH", "HTTP_PROXY", "HTTPS_PROXY", "LD_PRELOAD"):
        monkeypatch.setenv(key, "/synthetic-untrusted-setting")
    with image_files(tmp_path) as files: assert run(files).bits == 8


@pytest.mark.parametrize("defect", ["hash", "dimensions", "format", "copy", "upscale", "bits"])
def test_rejects_inconsistent_input_or_operation_without_claiming_output(tmp_path, defect):
    with image_files(tmp_path) as files:
        changes = {"hash": {"source_sha256": "0" * 64}, "dimensions": {"height": 185}, "format": {"format": "TIFF"},
            "copy": {"action": "COPY"}, "upscale": {"width": 2048, "height": 736}, "bits": {"bits": 16}}[defect]
        files = (*files[:2], replace(files[2], **changes), *files[3:])
        with pytest.raises(PackagingConversionError): run(files)
        assert files[5].read_bytes() == b"" and list(files[3].iterdir()) == []


@pytest.mark.parametrize("defect", ["nonempty", "writable-source", "hardlink", "public-cache"])
def test_requires_private_unchanged_source_exclusive_output_and_cache(tmp_path, defect):
    with image_files(tmp_path) as files:
        if defect == "nonempty": os.write(files[1], b"Existing output")
        elif defect == "writable-source": files[4].chmod(0o600)
        elif defect == "hardlink": os.link(files[5], tmp_path / "other-name")
        else: files[3].chmod(0o755)
        with pytest.raises(PackagingConversionError): run(files)
        assert files[5].read_bytes() == (b"Existing output" if defect == "nonempty" else b"")
        assert list(files[3].iterdir()) == []


def test_modified_or_absent_policy_fails_before_conversion(tmp_path, monkeypatch):
    policy = tmp_path / "policy.xml"; policy.write_text("<policymap/>"); monkeypatch.setattr(conversion, "POLICY", policy)
    with image_files(tmp_path) as files:
        with pytest.raises(PackagingConversionError, match="POLICY_MISMATCH"): run(files)
        policy.unlink()
        with pytest.raises(PackagingConversionError, match="RUNTIME_UNAVAILABLE"): run(files)
        assert files[5].read_bytes() == b""


@pytest.mark.parametrize("defect", ["empty", "corrupt", "dimensions", "depth", "source-change"])
def test_process_exit_zero_is_not_enough_to_report_success(tmp_path, monkeypatch, defect):
    with image_files(tmp_path, "PNG", 16) as files:
        def faulty(*args):
            if defect == "corrupt": os.write(files[1], b"Not an image")
            elif defect in {"dimensions", "depth", "source-change"}:
                with os.fdopen(os.dup(files[1]), "wb") as stream:
                    Image.new("I;16" if defect != "depth" else "L", (511 if defect == "dimensions" else 512, 184), 42).save(stream, format="PNG")
            if defect == "source-change": files[4].chmod(0o600); files[4].write_bytes(b"Changed input")
        monkeypatch.setattr(conversion, "_run", faulty)
        with pytest.raises(PackagingConversionError): run(files)
        assert list(files[3].iterdir()) == []


def test_actual_output_size_limit_stops_conversion_and_removes_cache(tmp_path):
    with image_files(tmp_path) as files:
        with pytest.raises(PackagingConversionError): run(files, max_bytes=64)
        assert files[5].stat().st_size <= 64 and list(files[3].iterdir()) == []


def test_actual_timeout_kills_and_reaps_the_child():
    with pytest.raises(PackagingConversionError, match="TIMEOUT"):
        conversion._run([sys.executable, "-c", "import time; time.sleep(10)"], (), .05)


def test_second_conversion_is_rejected_without_unbounded_queue(tmp_path):
    with image_files(tmp_path) as files:
        assert conversion.CONVERSION_SLOT.acquire(blocking=False)
        try:
            with pytest.raises(PackagingConversionError, match="BUSY"): run(files)
        finally: conversion.CONVERSION_SLOT.release()
        assert files[5].read_bytes() == b"" and run(files).width == 512


@pytest.mark.parametrize("argument", ["https://example.invalid/image.png", "label:forbidden", "mvg:fd:3", "svg:fd:3", "@/etc/passwd", "/etc/passwd", "png:/tmp/unplanned.png"])
def test_installed_policy_denies_network_pseudo_coders_indirect_and_path_reads(argument):
    conversion.verify_runtime()
    result = subprocess.run([conversion.EXECUTABLE, argument, "PNG:fd:1"], env=conversion._environment(), cwd="/",
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=5)
    # Diagnostics are synthetic test data; do not print them.
    assert result.returncode != 0
    assert b"security policy" in result.stderr or b"no decode delegate" in result.stderr


def test_policy_blocks_external_delegates_and_filters_and_caps_sequences():
    result = subprocess.run([conversion.EXECUTABLE, "-list", "policy"], env=conversion._environment(), cwd="/",
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=True)
    text = "\n".join(line.rstrip() for line in result.stdout.decode("utf-8").splitlines())
    assert "Policy: Delegate\n    rights: None\n    pattern: *" in text
    assert "Policy: Filter\n    rights: None\n    pattern: *" in text
    assert "name: list-length\n    value: 4" in text


def test_multiframe_tiff_is_rejected_before_conversion(tmp_path):
    with image_files(tmp_path, "TIFF") as files:
        files[4].chmod(0o600)
        first = Image.new("RGB", (1024, 368), "red"); second = Image.new("RGB", (1024, 368), "blue")
        first.save(files[4], format="TIFF", save_all=True, append_images=[second])
        files[4].chmod(0o400)
        operation = replace(files[2], source_sha256=hashlib.sha256(files[4].read_bytes()).hexdigest())
        with pytest.raises(PackagingConversionError, match="INPUT_MISMATCH"):
            run((*files[:2], operation, *files[3:]))
        assert files[5].read_bytes() == b"" and list(files[3].iterdir()) == []


@pytest.mark.parametrize("width,height,side,expected", [(3500, 1259, 3072, (3072, 1105)), (1259, 3500, 3072, (1105, 3072)),
    (2048, 737, 1024, (1024, 369)), (2048, 736, 2048, (2048, 736))])
def test_actual_rounding_and_pixels_match_the_archived_resize_command(tmp_path, width, height, side, expected):
    with image_files(tmp_path, width=width, height=height) as files:
        operation = replace(files[2], width=expected[0], height=expected[1])
        proof = run((*files[:2], operation, *files[3:]))
        # Historical algorithm: a single aspect-preserving '-resize NxN'.
        # Use the same runtime, synthetic bytes and safe inherited descriptors;
        # no archived shell script or production files are executed.
        reference = tmp_path / "reference.png"
        with reference.open("w+b") as output:
            os.lseek(files[0], 0, os.SEEK_SET)
            result = subprocess.run([conversion.EXECUTABLE, f"PNG:fd:{files[0]}", "-resize", f"{side}x{side}", f"PNG:fd:{output.fileno()}"],
                pass_fds=(files[0], output.fileno()), env=conversion._environment(), cwd=files[3],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            assert result.returncode == 0
        with Image.open(reference) as old, Image.open(files[5]) as new:
            assert old.size == new.size == expected and old.convert("RGB").tobytes() == new.convert("RGB").tobytes()
        assert (proof.width, proof.height) == expected


def test_16_bit_color_png_is_not_silently_quantized(tmp_path):
    import struct
    import zlib
    # Pillow cannot encode RGB16; construct a minimal standards-compliant PNG.
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    with image_files(tmp_path, bits=16) as files:
        row = b"\x00" + b"".join(struct.pack(">HHH", x * 61, x * 31, x * 17) for x in range(1024))
        value = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1024, 368, 16, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(row * 368)) + chunk(b"IEND", b"")
        files[4].chmod(0o600); files[4].write_bytes(value); files[4].chmod(0o400)
        operation = replace(files[2], source_sha256=hashlib.sha256(value).hexdigest())
        proof = run((*files[:2], operation, *files[3:]))
        encoded = files[5].read_bytes()
        assert proof.bits == 16 and encoded[24] == 16 and encoded[25] == 2


def test_policy_rejects_reading_an_existing_valid_image_by_path(tmp_path):
    with image_files(tmp_path) as files:
        result = subprocess.run([conversion.EXECUTABLE, "PNG:" + str(files[4]), f"PNG:fd:{files[1]}"], pass_fds=(files[1],),
            env=conversion._environment(), cwd=files[3], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=5)
        assert result.returncode != 0 and b"security policy" in result.stderr and files[5].read_bytes() == b""
