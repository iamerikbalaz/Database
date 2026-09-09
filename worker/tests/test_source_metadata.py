import hashlib
import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.preflight import ZipPolicy, preflight_material
from app.source_metadata import normalize_hex, parse_source_metadata


FIXTURE = Path(__file__).parent / "fixtures" / "source_metadata_minimal.txt"
BOUNDARY = datetime(2026, 3, 4, tzinfo=ZoneInfo("Europe/Prague"))


def material(tmp_path, raw=None):
    path = tmp_path / "Example material"
    path.mkdir()
    (path / "16K").mkdir()
    if raw is not None:
        (path / "metadata.txt").write_bytes(raw)
    return path


def inspect(path):
    return parse_source_metadata(path, allowed_root=path, boundary=BOUNDARY)


def codes(result):
    return {item.code for item in result.warnings}


def test_observed_valid_text_is_partial_without_hex(tmp_path):
    raw = FIXTURE.read_bytes()
    result = inspect(material(tmp_path, raw))
    assert result.source_filename == "metadata.txt"
    assert result.status == "WARNING"
    assert result.width_cm == Decimal("12")
    assert result.height_cm == Decimal("34")
    assert result.hex_color is None
    assert codes(result) == {"HEX_COLOR_MISSING"}
    assert result.sha256 == hashlib.sha256(raw).hexdigest()
    assert result.can_continue and not result.errors


@pytest.mark.parametrize("raw,status", [
    (None, "MISSING"), (b"", "INVALID"), (b" \r\n", "INVALID"),
    (b"{broken", "INVALID"), (b"\xff", "INVALID"),
    (b"texture size: cm", "INVALID"),
    (b"texture size: 1x2 cm\ntexture size: 3x4 cm", "INVALID"),
    (b"texture size: NaNxInfinity cm", "INVALID"),
    (b"texture size: 1,5x2 cm", "INVALID"),
])
def test_metadata_problems_are_nonblocking(tmp_path, raw, status):
    result = inspect(material(tmp_path, raw))
    assert result.status == status
    assert result.warnings and not result.errors and result.can_continue
    assert result.sha256 == (hashlib.sha256(raw).hexdigest() if raw is not None else None)


def test_missing_dimensions(tmp_path):
    result = inspect(material(tmp_path, b"unrecognized text"))
    assert "SOURCE_METADATA_DIMENSIONS_MISSING" in codes(result)
    assert result.width_cm is result.height_cm is None
    assert result.can_continue


@pytest.mark.parametrize("width,height", [("0", "2"), ("-1", "2"), ("2", "0"), ("2", "-3")])
def test_nonpositive_dimensions_keep_available_value(tmp_path, width, height):
    result = inspect(material(tmp_path, f"texture size: {width}x{height} cm".encode()))
    assert result.width_cm == (Decimal(width) if Decimal(width) > 0 else None)
    assert result.height_cm == (Decimal(height) if Decimal(height) > 0 else None)
    assert "SOURCE_METADATA_INVALID_DIMENSION" in codes(result)
    assert result.can_continue


def test_exact_decimal_dimensions_and_serialization(tmp_path):
    raw = b"texture size: 0.10000000000000000001x2.500 cm\r\n"
    result = inspect(material(tmp_path, raw))
    assert isinstance(result.width_cm, Decimal)
    assert result.width_cm == Decimal("0.10000000000000000001")
    assert result.height_cm == Decimal("2.500")
    payload = json.loads(json.dumps(result.to_dict()))
    assert payload["width_cm"] == "0.10000000000000000001"
    assert payload["height_cm"] == "2.500"
    assert result.sha256 == hashlib.sha256(raw).hexdigest()
    assert result.sha256 != hashlib.sha256(raw.strip()).hexdigest()


@pytest.mark.parametrize("value,expected", [("aBc123", "#ABC123"), ("#00ff00", "#00FF00")])
def test_hex_normalization_only_not_undocumented_extraction(value, expected):
    assert normalize_hex(value) == expected


@pytest.mark.parametrize("value", ["", "#123", "#12345678", "#GG0000", None, 123456])
def test_invalid_hex_normalization(value):
    with pytest.raises(ValueError):
        normalize_hex(value)


def test_unknown_color_line_is_not_silently_interpreted(tmp_path):
    result = inspect(material(tmp_path, b"texture size: 1x2 cm\ncolor: #bad"))
    assert result.hex_color is None
    assert "SOURCE_METADATA_UNRECOGNIZED_CONTENT" in codes(result)
    assert result.can_continue


def test_only_root_source_file_is_read(tmp_path, monkeypatch):
    path = material(tmp_path)
    manifest = b'{"WEB_APP_PART":{},"DESKTOP_APP_PART":{},}'
    for other in (path / "web-manifest.json", path / "metadata.json", path / "16K" / "metadata.txt"):
        other.write_bytes(manifest)
    original = Path.open
    def root_only(self, *args, **kwargs):
        assert self == path / "metadata.txt"
        return original(self, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", root_only)
        assert inspect(path).status == "MISSING"
    (path / "metadata.txt").write_bytes(manifest)
    result = inspect(path)
    assert result.status == "INVALID"
    assert result.width_cm is result.height_cm is result.hex_color is None
    assert result.can_continue


def test_unreadable_source(tmp_path, monkeypatch):
    path = material(tmp_path, b"texture size: 1x2 cm")
    def denied(*args, **kwargs):
        raise PermissionError("test")
    monkeypatch.setattr(Path, "open", denied)
    result = inspect(path)
    assert result.status == "INVALID"
    assert result.sha256 is None and result.can_continue


def test_metadata_directory_and_limit(tmp_path):
    path = material(tmp_path)
    source = path / "metadata.txt"
    source.mkdir()
    assert inspect(path).status == "INVALID"
    source.rmdir()
    source.write_bytes(b" " * (4 * 1024 * 1024 + 1))
    result = inspect(path)
    assert result.status == "INVALID" and result.sha256 is None and result.can_continue


@pytest.mark.parametrize("delta,policy", [(-1, ZipPolicy.LEGACY_BEFORE_2026_03_04),
    (0, ZipPolicy.CURRENT_ON_OR_AFTER_2026_03_04),
    (86400, ZipPolicy.CURRENT_ON_OR_AFTER_2026_03_04)])
def test_master_16k_policy_is_shared(tmp_path, delta, policy):
    path = material(tmp_path, FIXTURE.read_bytes())
    (path / "4K").mkdir()
    ns = (int(BOUNDARY.timestamp()) + delta) * 1_000_000_000
    os.utime(path / "16K", ns=(ns, ns))
    result = inspect(path)
    reference = preflight_material(path, allowed_root=path, boundary=BOUNDARY)
    assert result.master_resolution == reference.master_resolution == "16K"
    assert result.master_modified_at == reference.master_modified_at
    assert result.selected_zip_policy == reference.selected_zip_policy == policy
    assert result.can_continue


def test_read_only_contents_and_last_write_time(tmp_path, monkeypatch):
    path = material(tmp_path, FIXTURE.read_bytes())
    (path / "16K" / "map.txt").write_bytes(b"synthetic")
    def snapshot():
        return {str(p.relative_to(path)): (p.stat().st_mtime_ns, p.stat().st_ctime_ns,
                p.read_bytes() if p.is_file() else None) for p in [path, *path.rglob("*")]}
    before = snapshot()
    original = Path.open
    def read_only(self, mode="r", *args, **kwargs):
        assert mode == "rb"
        return original(self, mode, *args, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail("Mutation attempted")
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", read_only)
        for name in ("rename", "replace", "remove", "unlink", "mkdir", "rmdir", "utime"):
            patch.setattr(os, name, forbidden)
        assert inspect(path).can_continue
        assert inspect(path).can_continue
    assert snapshot() == before


def test_material_errors_remain_blocking(tmp_path):
    result = parse_source_metadata(tmp_path / "absent", allowed_root=tmp_path)
    assert result.status == "NOT_SCANNED" and result.errors and not result.can_continue


@pytest.mark.parametrize("color,expected,code", [
    ({"hex": "#aBc123"}, "#ABC123", None),
    ({"hex": "00ff00"}, "#00FF00", None),
    ({"hex": "#bad"}, None, "HEX_COLOR_INVALID"),
    ({"hex": None}, None, "HEX_COLOR_INVALID"),
    ({}, None, "HEX_COLOR_MISSING"),
])
def test_explicit_json_hex(tmp_path, color, expected, code):
    raw = json.dumps({"COLOR": color, "TEXTURE_SIZE": {"cm": {"width": 12, "height": 34}}}).encode()
    result = inspect(material(tmp_path, raw))
    assert result.hex_color == expected
    assert result.status == ("WARNING" if code else "VALID")
    assert codes(result) == ({code} if code else set())
    assert result.width_cm == Decimal("12")
    assert result.height_cm == Decimal("34")
    assert result.sha256 == hashlib.sha256(raw).hexdigest()
    assert result.can_continue


def test_no_master_still_reads_and_hashes_metadata(tmp_path):
    raw = FIXTURE.read_bytes()
    path = material(tmp_path, raw)
    (path / "16K").rmdir()
    result = inspect(path)
    assert result.status == "WARNING"
    assert result.width_cm == Decimal("12") and result.height_cm == Decimal("34")
    assert result.sha256 == hashlib.sha256(raw).hexdigest()
    assert [f.code for f in result.master_errors] == ["NO_RESOLUTION"]
    assert result.master_resolution is None and result.selected_zip_policy is None
    assert result.can_continue and not result.errors


def test_json_precision_and_no_random_hex(tmp_path):
    raw = b'{"note":"#AABBCC","TEXTURE_SIZE":{"cm":{"width":0.10000000000000000001,"height":2.50}}}'
    result = inspect(material(tmp_path, raw))
    assert result.width_cm == Decimal("0.10000000000000000001")
    assert result.hex_color is None and codes(result) == {"HEX_COLOR_MISSING"}


@pytest.mark.parametrize("raw", [b'{"COLOR":{},"COLOR":{}}', b'{"COLOR":{"hex":NaN}}',
    b'{"WEB_APP_PART":{},"DESKTOP_APP_PART":{}}', b'[]'])
def test_strict_json_and_manifest_rejection(tmp_path, raw):
    result = inspect(material(tmp_path, raw))
    assert result.status == "INVALID" and result.can_continue
    assert result.sha256 == hashlib.sha256(raw).hexdigest()


def test_all_five_statuses(tmp_path):
    from app.source_metadata import SourceMetadataResult
    assert SourceMetadataResult().status == "NOT_SCANNED"
    path = material(tmp_path)
    assert inspect(path).status == "MISSING"
    source = path / "metadata.txt"
    for raw, expected in [(b'{broken', "INVALID"), (FIXTURE.read_bytes(), "WARNING"),
        (b'{"COLOR":{"hex":"#abcdef"},"TEXTURE_SIZE":{"cm":{"width":1,"height":2}}}', "VALID")]:
        source.write_bytes(raw)
        assert inspect(path).status == expected


def test_rejected_root_never_reads_metadata(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unsafe root must not be read")
    monkeypatch.setattr(Path, "open", forbidden)
    result = parse_source_metadata(tmp_path.parent, allowed_root=tmp_path)
    assert result.status == "NOT_SCANNED" and not result.can_continue
