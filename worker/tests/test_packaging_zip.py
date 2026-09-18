from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import os
import stat
import struct
import zipfile
import zlib

import pytest

from app import packaging_zip
from app.packaging_zip import PackagingZipError, ZipEntry, create_verified_zip
from app.preflight import ZipPolicy
from app.secure_filesystem import secure_filesystem_access_supported

pytestmark = pytest.mark.skipif(not secure_filesystem_access_supported(), reason="Linux private ZIP descriptors required")
CURRENT = ZipPolicy.CURRENT_ON_OR_AFTER_2026_03_04.value
LEGACY = ZipPolicy.LEGACY_BEFORE_2026_03_04.value
STAMP = int(datetime(2026, 9, 18, 12, 13, 15, tzinfo=timezone.utc).timestamp()) * 1_000_000_000
ROOT = "SYNTHETIC_LONG_PREFIX_0001_G03_1K"


@pytest.fixture
def files(tmp_path):
    content = {"metadata.json": b'{"WEB_APP_PART":{},"DESKTOP_APP_PART":{}}\n',
        "1K/SYNTHETIC_LONG_PREFIX_0001_G03_COL_1K.png": b"Synthetic already-verified map bytes\x00" * 40,
        "1K/metadata.txt": b"\xef\xbb\xbfSynthetic unchanged production metadata\r\n",
        "PREVIEW/Český náhled.txt": b"Synthetic opaque preview bytes", "PREVIEW/empty": b""}
    entries = [ZipEntry("1K/", 0, None, STAMP), ZipEntry("PREVIEW/", 0, None, STAMP), ZipEntry("PREVIEW/empty-directory/", 0, None, STAMP)]
    paths = {}
    for index, (name, value) in enumerate(content.items()):
        path = tmp_path / ("input-" + str(index)); path.write_bytes(value); os.utime(path, ns=(STAMP, STAMP)); path.chmod(0o400)
        paths[name] = path; entries.append(ZipEntry(name, len(value), hashlib.sha256(value).hexdigest(), STAMP))
    @contextmanager
    def open_entry(name):
        fd = os.open(paths[name], os.O_RDONLY | os.O_NOFOLLOW)
        try: yield fd
        finally: os.close(fd)
    target = tmp_path / "archive.zip"; fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try: yield fd, target, tuple(entries), open_entry, paths, content
    finally: os.close(fd)


def create(files, **changes):
    arguments = dict(root=ROOT, entries=files[2], open_entry=files[3], policy=CURRENT, storage_timezone="Europe/Prague", root_modified_ns=STAMP)
    arguments.update(changes)
    return create_verified_zip(files[0], **arguments)


@pytest.mark.parametrize("policy,stamp", [(CURRENT, (2026, 9, 18, 14, 13, 14)), (LEGACY, (2026, 1, 1, 0, 0, 0))])
def test_complete_zip_layout_exact_bytes_crc_hash_unicode_and_historical_timestamps(files, policy, stamp):
    before = {name: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode) for name, path in files[4].items()}
    proof = create(files, policy=policy)
    assert proof.filename == ROOT + ".zip" and proof.size == files[1].stat().st_size
    assert proof.sha256 == hashlib.sha256(files[1].read_bytes()).hexdigest() and proof.policy == policy
    assert stat.S_IMODE(files[1].stat().st_mode) == 0o400
    with zipfile.ZipFile(files[1]) as archive:
        assert archive.namelist() == [ROOT + "/", *sorted(ROOT + "/" + entry.path for entry in files[2])]
        assert archive.testzip() is None
        assert all(entry.date_time == stamp and not entry.flag_bits & 1 for entry in archive.infolist())
        for name, value in files[5].items():
            assert archive.read(ROOT + "/" + name) == value
            item = next(item for item in proof.entries if item.path == ROOT + "/" + name)
            assert item.sha256 == hashlib.sha256(value).hexdigest() and item.crc32 == zlib.crc32(value)
        assert not any("SOURCE" in name for name in archive.namelist())
    assert {name: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode) for name, path in files[4].items()} == before


def test_current_timestamps_use_explicit_storage_zone_instead_of_host_zone(files, monkeypatch):
    monkeypatch.setenv("TZ", "Pacific/Honolulu")
    proof = create(files, storage_timezone="UTC")
    assert all(item.date_time == (2026, 9, 18, 12, 13, 14) for item in proof.entries)


@pytest.mark.parametrize("defect", ["traversal", "absolute", "backslash", "case-collision", "file-directory-collision", "missing-parent", "duplicate", "bad-hash", "negative-size", "directory-size", "policy", "zone", "root"])
def test_unsafe_or_inconsistent_layout_is_rejected_before_output(files, defect):
    entries = list(files[2]); changes = {}
    if defect in {"traversal", "absolute", "backslash"}:
        entries[0] = replace(entries[0], path={"traversal": "../", "absolute": "/root/", "backslash": "..\\root/"}[defect])
    elif defect == "case-collision": entries.append(replace(entries[-1], path=entries[-1].path.upper()))
    elif defect == "file-directory-collision": entries.append(ZipEntry("PREVIEW", 0, hashlib.sha256(b"").hexdigest(), STAMP))
    elif defect == "missing-parent": entries.pop(1)
    elif defect == "duplicate": entries.append(entries[-1])
    elif defect == "bad-hash": entries[-1] = replace(entries[-1], sha256="bad")
    elif defect == "negative-size": entries[-1] = replace(entries[-1], size=-1)
    elif defect == "directory-size": entries[0] = replace(entries[0], size=1)
    elif defect == "policy": changes["policy"] = "automatic"
    elif defect == "zone": changes["storage_timezone"] = "../unexpected"
    else: changes["root"] = "../escape"
    with pytest.raises(PackagingZipError): create(files, entries=tuple(entries), **changes)
    assert files[1].read_bytes() == b""


@pytest.mark.parametrize("defect", ["bytes", "size", "mtime", "writable", "hardlink"])
def test_archive_does_not_accept_unverified_or_changed_sources(files, defect, tmp_path):
    source = next(iter(files[4].values())); original = source.read_bytes()
    if defect == "bytes": source.chmod(0o600); source.write_bytes(b"x" * len(original)); os.utime(source, ns=(STAMP, STAMP)); source.chmod(0o400)
    elif defect == "size": source.chmod(0o600); source.write_bytes(original + b"x"); source.chmod(0o400)
    elif defect == "mtime": os.utime(source, ns=(STAMP + 1, STAMP + 1))
    elif defect == "writable": source.chmod(0o600)
    else: os.link(source, tmp_path / "hardlink")
    with pytest.raises(PackagingZipError, match="SOURCE_MISMATCH"): create(files)
    assert stat.S_IMODE(files[1].stat().st_mode) == 0o600


@pytest.mark.parametrize("limits", [{"max_bytes": 100}, {"max_expanded_bytes": 10}, {"max_seconds": .000001}])
def test_compressed_expanded_and_time_limits_are_enforced(files, limits):
    with pytest.raises(PackagingZipError): create(files, **limits)
    if "max_bytes" in limits: assert files[1].stat().st_size <= limits["max_bytes"]
    assert stat.S_IMODE(files[1].stat().st_mode) == 0o600


@pytest.mark.parametrize("defect", ["nonempty", "hardlink", "mode"])
def test_target_must_be_new_and_private(files, defect, tmp_path):
    if defect == "nonempty": os.write(files[0], b"Keep existing bytes")
    elif defect == "hardlink": os.link(files[1], tmp_path / "other-name")
    else: files[1].chmod(0o644)
    before = files[1].read_bytes()
    with pytest.raises(PackagingZipError, match="UNSAFE_TARGET"): create(files)
    assert files[1].read_bytes() == before


@pytest.mark.parametrize("corruption", ["crc", "name", "timestamp", "trailing", "entry-bytes", "attribute"])
def test_post_write_verification_detects_corrupted_archive(files, monkeypatch, corruption):
    verify = packaging_zip._verify
    def corrupt(stream, expected, size):
        stream.stream.seek(0); value = bytearray(stream.stream.read())
        central = value.index(b"PK\x01\x02")
        if corruption == "crc": value[central + 16] ^= 1
        elif corruption == "name": value[central + 46] = ord("X")
        elif corruption == "timestamp": value[central + 12] ^= 2
        elif corruption == "trailing": value += b"trailing junk"
        elif corruption == "attribute": value[central + 38] ^= 1
        else:
            with zipfile.ZipFile(files[1]) as archive: info = next(item for item in archive.infolist() if not item.is_dir() and item.file_size > 20)
            offset = info.header_offset; name_length, extra_length = struct.unpack_from("<HH", value, offset + 26)
            value[offset + 30 + name_length + extra_length + 2] ^= 8
        stream.stream.seek(0); stream.stream.write(value); stream.stream.flush()
        return verify(stream, expected, len(value))
    monkeypatch.setattr(packaging_zip, "_verify", corrupt)
    with pytest.raises(PackagingZipError): create(files)
    assert stat.S_IMODE(files[1].stat().st_mode) == 0o600


def test_small_zip64_fixture_exercises_large_file_format_without_allocating_gigabytes(files, monkeypatch):
    monkeypatch.setattr(zipfile, "ZIP64_LIMIT", 128)
    proof = create(files)
    with zipfile.ZipFile(files[1]) as archive:
        assert archive.testzip() is None and any(item.extract_version == 45 for item in archive.infolist())
    assert proof.sha256 == hashlib.sha256(files[1].read_bytes()).hexdigest()


def test_unchanged_sized_source_rewrite_during_read_cannot_yield_success(files, monkeypatch):
    source = next(iter(files[4].values())); inode = source.stat().st_ino; read = os.read; changed = False
    def racing(fd, count):
        nonlocal changed
        value = read(fd, count)
        if not changed and os.fstat(fd).st_ino == inode and value:
            changed = True; source.chmod(0o600); source.write_bytes(b"x" * len(value)); source.chmod(0o400)
        return value
    monkeypatch.setattr(os, "read", racing)
    with pytest.raises(PackagingZipError, match="SOURCE_MISMATCH"): create(files)
    assert changed
