"""Race-safe, descriptor-relative material inspection for POSIX workers."""

import errno
import os
import stat
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from app.preflight import (
    MAX_METADATA_BYTES,
    RESOLUTION_PATTERN,
    STANDARD_RESOLUTIONS,
    Finding,
    configured_policy_boundary,
    select_zip_policy,
)
from app.source_metadata import SourceMetadataResult, parse_source_metadata_bytes


_HAS_DIR_FD_OPEN = os.open in os.supports_dir_fd
_HAS_DIR_FD_STAT = os.stat in os.supports_dir_fd
_HAS_FD_LISTDIR = os.listdir in os.supports_fd


class SecureFilesystemAccessUnavailable(RuntimeError):
    """The runtime cannot provide descriptor-relative no-follow access."""


class MaterialsRootUnavailable(RuntimeError):
    """The configured root cannot be securely opened."""


class MaterialFolderNotFound(RuntimeError):
    """A requested relative component does not exist."""


class UnsafeMaterialPath(RuntimeError):
    """A component is linked, replaced, inaccessible, or not a directory."""


def secure_filesystem_access_supported() -> bool:
    """Return whether all primitives required by the fail-closed path are present."""
    return (
        os.name == "posix"
        and all(hasattr(os, name) for name in (
            "O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK", "O_RDONLY"
        ))
        and _HAS_DIR_FD_OPEN
        and _HAS_DIR_FD_STAT
        and _HAS_FD_LISTDIR
    )


def _close(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _metadata_flags() -> int:
    # O_NONBLOCK prevents a malicious FIFO from blocking before fstat rejects it.
    return os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def _defense_in_depth_check(root: Path, parts: tuple[str, ...]) -> None:
    """Retain canonical/reparse checks without using their result for file access."""
    try:
        root_info = root.lstat()
        if stat.S_ISLNK(root_info.st_mode) or bool(
            getattr(root_info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise MaterialsRootUnavailable("MATERIALS_ROOT must not be a link")
        canonical_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise MaterialsRootUnavailable("MATERIALS_ROOT is unavailable") from exc

    current = root
    for part in parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise UnsafeMaterialPath("Material path cannot be safely inspected") from exc
        if stat.S_ISLNK(info.st_mode) or bool(
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise UnsafeMaterialPath("Linked path components are not allowed")

    try:
        canonical_material = current.resolve(strict=True)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise UnsafeMaterialPath("Material path cannot be safely inspected") from exc
    if not canonical_material.is_relative_to(canonical_root):
        raise UnsafeMaterialPath("Material path resolves outside MATERIALS_ROOT")


def _read_bounded(fd: int) -> bytes:
    remaining = MAX_METADATA_BYTES + 1
    chunks: list[bytes] = []
    while remaining:
        chunk = os.read(fd, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _format_modified_at(modified_ns: int) -> str:
    seconds, nanos = divmod(modified_ns, 1_000_000_000)
    instant = datetime.fromtimestamp(seconds, timezone.utc)
    return instant.strftime("%Y-%m-%dT%H:%M:%S") + f".{nanos:09d}+00:00"


def _resolution_info(material_fd: int) -> tuple[
    str | None, str | None, int | None, list[Finding], list[Finding]
]:
    warnings: list[Finding] = []
    errors: list[Finding] = []
    resolutions: list[tuple[str, os.stat_result]] = []
    try:
        names = os.listdir(material_fd)
    except OSError:
        return None, None, None, warnings, [Finding(
            "MATERIAL_INSPECTION_FAILED", "", "Material folder could not be listed"
        )]

    for name in names:
        if not RESOLUTION_PATTERN.fullmatch(name):
            continue
        try:
            resolution_fd = os.open(name, _directory_flags(), dir_fd=material_fd)
        except OSError:
            try:
                info = os.stat(name, dir_fd=material_fd, follow_symlinks=False)
            except OSError:
                errors.append(Finding(
                    "MATERIAL_INSPECTION_FAILED", name,
                    "Resolution changed during secure inspection",
                ))
                continue
            if stat.S_ISREG(info.st_mode):
                continue
            errors.append(Finding(
                "UNSAFE_RESOLUTION", name,
                "Resolution is linked, inaccessible, or not a plain directory",
            ))
            continue
        try:
            try:
                info = os.fstat(resolution_fd)
            except OSError:
                errors.append(Finding(
                    "MATERIAL_INSPECTION_FAILED", name,
                    "Resolution descriptor could not be inspected",
                ))
                continue
            if not stat.S_ISDIR(info.st_mode):
                errors.append(Finding(
                    "UNSAFE_RESOLUTION", name, "Resolution is not a directory"
                ))
                continue
            resolutions.append((name, info))
            if name not in STANDARD_RESOLUTIONS:
                warnings.append(Finding(
                    "NON_STANDARD_RESOLUTION", name,
                    f"{name} is usable but is not a standard resolution",
                ))
        finally:
            _close(resolution_fd)

    resolutions.sort(key=lambda item: (len(item[0]), item[0]))
    if not resolutions:
        errors.append(Finding("NO_RESOLUTION", "", "No resolution directory found"))
        return None, None, None, warnings, errors
    if errors:
        return None, None, None, warnings, errors

    master_name, master_info = resolutions[-1]
    modified_ns = master_info.st_mtime_ns
    return (
        master_name,
        _format_modified_at(modified_ns),
        modified_ns,
        warnings,
        errors,
    )


def _metadata_bytes(material_fd: int) -> tuple[bytes | None, tuple[str, str] | None]:
    try:
        metadata_fd = os.open("metadata.txt", _metadata_flags(), dir_fd=material_fd)
    except FileNotFoundError:
        return None, ("MISSING", "Root metadata.txt does not exist")
    except OSError as exc:
        status_name = "UNSAFE_FILE" if exc.errno in {errno.ELOOP, errno.ENOTDIR} else "UNREADABLE"
        message = (
            "Source metadata must be a plain regular file"
            if status_name == "UNSAFE_FILE"
            else "Root metadata.txt could not be securely opened"
        )
        return None, (status_name, message)

    try:
        try:
            info = os.fstat(metadata_fd)
        except OSError:
            return None, ("UNREADABLE", "Root metadata.txt could not be inspected")
        if not stat.S_ISREG(info.st_mode):
            return None, ("UNSAFE_FILE", "Source metadata must be a plain regular file")
        if info.st_size > MAX_METADATA_BYTES:
            return None, (
                "TOO_LARGE",
                "Source metadata exceeds the 4 MiB limit; no partial hash returned",
            )
        try:
            raw = _read_bounded(metadata_fd)
        except OSError:
            return None, ("UNREADABLE", "Root metadata.txt could not be read")
        if len(raw) > MAX_METADATA_BYTES:
            return None, ("TOO_LARGE", "Source metadata grew beyond the 4 MiB limit")
        return raw, None
    finally:
        _close(metadata_fd)


def inspect_material_secure(
    root: Path,
    parts: tuple[str, ...],
    *,
    boundary: datetime | None = None,
) -> SourceMetadataResult:
    """Inspect through one no-follow descriptor chain, closing every descriptor."""
    if not secure_filesystem_access_supported():
        raise SecureFilesystemAccessUnavailable(
            "Descriptor-relative no-follow filesystem access is unavailable"
        )

    boundary = boundary if boundary is not None else configured_policy_boundary()
    try:
        root_fd = os.open(root, _directory_flags())
    except OSError as exc:
        raise MaterialsRootUnavailable("MATERIALS_ROOT cannot be securely opened") from exc

    with ExitStack() as descriptors:
        descriptors.callback(_close, root_fd)
        try:
            root_info = os.fstat(root_fd)
        except OSError as exc:
            raise MaterialsRootUnavailable(
                "MATERIALS_ROOT descriptor could not be inspected"
            ) from exc
        if not stat.S_ISDIR(root_info.st_mode):
            raise MaterialsRootUnavailable("MATERIALS_ROOT is not a directory")

        _defense_in_depth_check(root, parts)
        material_fd = root_fd
        for part in parts:
            try:
                material_fd = os.open(part, _directory_flags(), dir_fd=material_fd)
            except FileNotFoundError as exc:
                raise MaterialFolderNotFound("Material folder does not exist") from exc
            except OSError as exc:
                raise UnsafeMaterialPath(
                    "Material component is linked, inaccessible, or not a directory"
                ) from exc
            descriptors.callback(_close, material_fd)
            try:
                component_info = os.fstat(material_fd)
            except OSError as exc:
                raise UnsafeMaterialPath(
                    "Material component descriptor could not be inspected"
                ) from exc
            if not stat.S_ISDIR(component_info.st_mode):
                raise UnsafeMaterialPath("Material component is not a directory")

        master, modified_at, modified_ns, master_warnings, master_errors = (
            _resolution_info(material_fd)
        )
        policy = (
            select_zip_policy(modified_ns, boundary=boundary)
            if modified_ns is not None else None
        )
        raw, metadata_error = _metadata_bytes(material_fd)
        return parse_source_metadata_bytes(
            raw,
            metadata_error=metadata_error,
            master_resolution=master,
            master_modified_at=modified_at,
            selected_zip_policy=policy,
            master_warnings=master_warnings,
            master_errors=master_errors,
        )
