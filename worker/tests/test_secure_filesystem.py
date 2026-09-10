import errno
import os
from pathlib import Path

import pytest

import app.secure_filesystem as secure
from app.secure_filesystem import (
    SecureFilesystemAccessUnavailable,
    UnsafeMaterialPath,
    inspect_material_secure,
    secure_filesystem_access_supported,
)


REQUIRES_SECURE_FILESYSTEM = pytest.mark.skipif(
    not secure_filesystem_access_supported(),
    reason="descriptor-relative O_NOFOLLOW access is unavailable",
)


def make_material(root: Path, raw: bytes = b"texture size: 12x34 cm") -> Path:
    material = root / "material"
    material.mkdir()
    (material / "4K").mkdir()
    (material / "metadata.txt").write_bytes(raw)
    return material


@REQUIRES_SECURE_FILESYSTEM
def test_initial_directory_symlink_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "material").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafeMaterialPath):
        inspect_material_secure(tmp_path, ("material",))


@REQUIRES_SECURE_FILESYSTEM
def test_metadata_symlink_is_rejected(tmp_path):
    material = make_material(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside secret")
    (material / "metadata.txt").unlink()
    (material / "metadata.txt").symlink_to(outside)

    result = inspect_material_secure(tmp_path, ("material",))

    assert result.status == "INVALID"
    assert result.raw_content is None
    assert {finding.code for finding in result.warnings} == {
        "SOURCE_METADATA_UNSAFE_FILE"
    }
    assert result.can_continue


@REQUIRES_SECURE_FILESYSTEM
def test_directory_replaced_after_initial_check_stays_on_open_descriptor(
    tmp_path, monkeypatch,
):
    material = make_material(tmp_path, b"texture size: 12x34 cm")
    outside = tmp_path / "outside"
    outside.mkdir()
    make_material(outside, b"texture size: 98x76 cm")
    outside_material = outside / "material"
    anchored = tmp_path / "anchored-material"
    original_listdir = os.listdir
    swapped = False

    def swap_then_list(directory):
        nonlocal swapped
        if isinstance(directory, int) and not swapped:
            swapped = True
            material.rename(anchored)
            material.symlink_to(outside_material, target_is_directory=True)
        return original_listdir(directory)

    monkeypatch.setattr(os, "listdir", swap_then_list)
    result = inspect_material_secure(tmp_path, ("material",))

    assert swapped
    assert result.raw_content == "texture size: 12x34 cm"
    assert result.width_cm is not None and str(result.width_cm) == "12"


@REQUIRES_SECURE_FILESYSTEM
def test_metadata_replaced_after_initial_check_is_not_followed(tmp_path, monkeypatch):
    material = make_material(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"texture size: 98x76 cm")
    original_resolution_info = secure._resolution_info

    def swap_after_scan(material_fd):
        result = original_resolution_info(material_fd)
        (material / "metadata.txt").unlink()
        (material / "metadata.txt").symlink_to(outside)
        return result

    monkeypatch.setattr(secure, "_resolution_info", swap_after_scan)
    result = inspect_material_secure(tmp_path, ("material",))

    assert result.status == "INVALID"
    assert result.raw_content is None
    assert {finding.code for finding in result.warnings} == {
        "SOURCE_METADATA_UNSAFE_FILE"
    }


@REQUIRES_SECURE_FILESYSTEM
def test_read_uses_already_open_metadata_descriptor(tmp_path, monkeypatch):
    material = make_material(tmp_path)
    metadata = material / "metadata.txt"
    moved = material / "opened-metadata.txt"
    original_open = os.open
    original_fstat = os.fstat
    metadata_fd = None
    swapped = False

    def track_open(path, flags, *args, **kwargs):
        nonlocal metadata_fd
        fd = original_open(path, flags, *args, **kwargs)
        if path == "metadata.txt":
            metadata_fd = fd
        return fd

    def swap_after_fstat(fd):
        nonlocal swapped
        info = original_fstat(fd)
        if fd == metadata_fd and not swapped:
            swapped = True
            metadata.rename(moved)
            metadata.write_bytes(b"texture size: 98x76 cm")
        return info

    monkeypatch.setattr(os, "open", track_open)
    monkeypatch.setattr(os, "fstat", swap_after_fstat)
    result = inspect_material_secure(tmp_path, ("material",))

    assert swapped
    assert result.raw_content == "texture size: 12x34 cm"
    assert metadata.read_bytes() == b"texture size: 98x76 cm"


@REQUIRES_SECURE_FILESYSTEM
@pytest.mark.parametrize("read_fails", [False, True])
def test_all_descriptors_are_closed_on_success_and_error(tmp_path, monkeypatch, read_fails):
    make_material(tmp_path)
    original_open = os.open
    opened: list[int] = []

    def track_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        opened.append(fd)
        return fd

    monkeypatch.setattr(os, "open", track_open)
    if read_fails:
        monkeypatch.setattr(secure, "_read_bounded", lambda fd: (_ for _ in ()).throw(
            OSError(errno.EIO, "simulated read failure")
        ))

    result = inspect_material_secure(tmp_path, ("material",))

    assert result.status == ("INVALID" if read_fails else "WARNING")
    assert opened
    for fd in set(opened):
        with pytest.raises(OSError) as exc_info:
            os.fstat(fd)
        assert exc_info.value.errno == errno.EBADF


@REQUIRES_SECURE_FILESYSTEM
def test_regular_metadata_is_loaded_without_path_open(tmp_path, monkeypatch):
    make_material(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("Path.open must not be used by secure preflight")

    monkeypatch.setattr(Path, "open", forbidden)
    result = inspect_material_secure(tmp_path, ("material",))

    assert result.raw_content == "texture size: 12x34 cm"
    assert result.status == "WARNING"


@REQUIRES_SECURE_FILESYSTEM
def test_directory_instead_of_metadata_is_rejected(tmp_path):
    material = make_material(tmp_path)
    (material / "metadata.txt").unlink()
    (material / "metadata.txt").mkdir()

    result = inspect_material_secure(tmp_path, ("material",))

    assert result.status == "INVALID"
    assert result.raw_content is None
    assert result.warnings[-1].code == "SOURCE_METADATA_UNSAFE_FILE"


@REQUIRES_SECURE_FILESYSTEM
def test_fifo_instead_of_metadata_is_rejected_without_blocking(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is unavailable")
    material = make_material(tmp_path)
    (material / "metadata.txt").unlink()
    os.mkfifo(material / "metadata.txt")

    result = inspect_material_secure(tmp_path, ("material",))

    assert result.status == "INVALID"
    assert result.raw_content is None
    assert result.warnings[-1].code == "SOURCE_METADATA_UNSAFE_FILE"


@REQUIRES_SECURE_FILESYSTEM
def test_oversized_metadata_is_rejected_before_read(tmp_path, monkeypatch):
    material = make_material(tmp_path)
    (material / "metadata.txt").write_bytes(b"x" * (4 * 1024 * 1024 + 1))

    def forbidden(*args, **kwargs):
        pytest.fail("Oversized metadata must not be read")

    monkeypatch.setattr(os, "read", forbidden)
    result = inspect_material_secure(tmp_path, ("material",))

    assert result.status == "INVALID"
    assert result.sha256 is None and result.raw_content is None
    assert result.warnings[-1].code == "SOURCE_METADATA_TOO_LARGE"


def test_unsupported_platform_fails_closed_before_open(tmp_path, monkeypatch):
    monkeypatch.setattr(secure, "secure_filesystem_access_supported", lambda: False)

    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported secure access must not open any path")

    monkeypatch.setattr(os, "open", forbidden)
    with pytest.raises(SecureFilesystemAccessUnavailable):
        inspect_material_secure(tmp_path, ("material",))
