"""Bounded PREVIEW-only browsing with no-follow descriptors and binding checks."""
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from threading import BoundedSemaphore
import time

from app.inventory import _safe_name, _signature
from app.preview_decode import MAX_SOURCE_BYTES, MAX_OUTPUT_BYTES, MAX_OUTPUT_SIDE, FORMATS
from app.secure_filesystem import _directory_flags, _metadata_flags, open_material_directory

EXTENSIONS = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "tif": "TIFF", "tiff": "TIFF", "webp": "WEBP"}
MAX_ITEMS = 64
MAX_DIRECTORY_ENTRIES = 512
MAX_TOTAL_BYTES = 512 * 1024**2
MAX_LIST_SECONDS = 15
MAX_WIRE_BYTES = 3 * 1024**2
PREVIEW_SLOTS = BoundedSemaphore(2)
ERROR_CODES = frozenset({"PREVIEW_BUSY", "PREVIEW_UNSAFE_ENTRY", "PREVIEW_UNSAFE_NAME", "PREVIEW_SOURCE_CHANGED",
    "PREVIEW_FILE_LIMIT", "PREVIEW_TOTAL_LIMIT", "PREVIEW_ENTRY_LIMIT", "PREVIEW_TIME_LIMIT", "PREVIEW_READ_FAILED",
    "PREVIEW_PIXEL_LIMIT", "PREVIEW_MULTIFRAME_UNSUPPORTED", "PREVIEW_MODE_UNSUPPORTED", "PREVIEW_OUTPUT_LIMIT",
    "PREVIEW_DECODER_UNAVAILABLE", "PREVIEW_RESOURCE_LIMIT", "PREVIEW_UNREADABLE", "PREVIEW_DECODER_FAILED",
    "PREVIEW_TIMEOUT", "PREVIEW_NOT_FOUND", "PREVIEW_EXTENSION_MISMATCH"})


class PreviewError(RuntimeError):
    def __init__(self, code):
        self.code = code if code in ERROR_CODES else "PREVIEW_READ_FAILED"
        super().__init__(self.code)


def valid_preview_name(name):
    return isinstance(name, str) and _safe_name(name) and "." in name and name.rsplit(".", 1)[-1].lower() in EXTENSIONS


@contextmanager
def _preview_directory(root, parts):
    with open_material_directory(root, parts) as material_fd:
        material_signature = _signature(os.fstat(material_fd))
        try:
            directory_fd = os.open("PREVIEW", _directory_flags(), dir_fd=material_fd)
        except FileNotFoundError:
            directory_fd = None
        except OSError:
            raise PreviewError("PREVIEW_UNSAFE_ENTRY") from None
        if directory_fd is None:
            yield None
            try: os.stat("PREVIEW", dir_fd=material_fd, follow_symlinks=False)
            except FileNotFoundError: pass
            else: raise PreviewError("PREVIEW_SOURCE_CHANGED")
        else:
            try:
                directory_signature = _signature(os.fstat(directory_fd))
                if directory_signature[0] != material_signature[0]: raise PreviewError("PREVIEW_UNSAFE_ENTRY")
                yield directory_fd
                if (_signature(os.fstat(directory_fd)) != directory_signature
                        or _signature(os.stat("PREVIEW", dir_fd=material_fd, follow_symlinks=False)) != directory_signature):
                    raise PreviewError("PREVIEW_SOURCE_CHANGED")
            finally: os.close(directory_fd)
        if _signature(os.fstat(material_fd)) != material_signature:
            raise PreviewError("PREVIEW_SOURCE_CHANGED")
        with open_material_directory(root, parts) as reopened:
            if _signature(os.fstat(reopened)) != material_signature:
                raise PreviewError("PREVIEW_SOURCE_CHANGED")


def _regular_file(directory_fd, name, device):
    before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_dev != device:
        raise PreviewError("PREVIEW_UNSAFE_ENTRY")
    if not 0 < before.st_size <= MAX_SOURCE_BYTES: raise PreviewError("PREVIEW_FILE_LIMIT")
    return before


def _verify_file(directory_fd, name, fd, before):
    if (_signature(os.fstat(fd)) != _signature(before)
            or _signature(os.stat(name, dir_fd=directory_fd, follow_symlinks=False)) != _signature(before)):
        raise PreviewError("PREVIEW_SOURCE_CHANGED")


def _bounded(operation, *args):
    if not PREVIEW_SLOTS.acquire(blocking=False): raise PreviewError("PREVIEW_BUSY")
    try:
        return operation(*args)
    except OSError:
        raise PreviewError("PREVIEW_READ_FAILED") from None
    finally: PREVIEW_SLOTS.release()


def list_previews(root, parts):
    return _bounded(_list_previews, root, parts)


def _list_previews(root, parts):
    deadline = time.monotonic() + MAX_LIST_SECONDS
    def check_time():
        if time.monotonic() > deadline: raise PreviewError("PREVIEW_TIME_LIMIT")
    items = []; ignored = 0; total = 0; signatures = {}
    with _preview_directory(root, parts) as directory_fd:
        if directory_fd is None:
            return {"schema_version": 1, "folder_name": parts[-1], "missing": True, "ignored_entries": 0, "items": []}
        device = os.fstat(directory_fd).st_dev
        names = []
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                check_time()
                if len(names) >= MAX_DIRECTORY_ENTRIES: raise PreviewError("PREVIEW_ENTRY_LIMIT")
                names.append(entry.name)
        for name in sorted(names):
            check_time()
            if not _safe_name(name): raise PreviewError("PREVIEW_UNSAFE_NAME")
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)) or info.st_dev != device:
                raise PreviewError("PREVIEW_UNSAFE_ENTRY")
            if stat.S_ISDIR(info.st_mode):
                ignored += 1; continue
            if info.st_nlink != 1: raise PreviewError("PREVIEW_UNSAFE_ENTRY")
            if not valid_preview_name(name):
                ignored += 1; continue
            if len(items) >= MAX_ITEMS: raise PreviewError("PREVIEW_ENTRY_LIMIT")
            before = _regular_file(directory_fd, name, device)
            total += before.st_size
            if total > MAX_TOTAL_BYTES: raise PreviewError("PREVIEW_TOTAL_LIMIT")
            fd = os.open(name, _metadata_flags(), dir_fd=directory_fd)
            try:
                _verify_file(directory_fd, name, fd, before)
                digest = hashlib.sha256(); count = 0
                while True:
                    check_time()
                    chunk = os.read(fd, 1024 * 1024)
                    if not chunk: break
                    count += len(chunk)
                    if count > before.st_size: raise PreviewError("PREVIEW_SOURCE_CHANGED")
                    digest.update(chunk)
                if count != before.st_size: raise PreviewError("PREVIEW_SOURCE_CHANGED")
                _verify_file(directory_fd, name, fd, before)
                items.append({"name": name, "size": count, "sha256": digest.hexdigest()})
                signatures[name] = _signature(before)
            finally: os.close(fd)
        for name, signature in signatures.items():
            check_time()
            if _signature(os.stat(name, dir_fd=directory_fd, follow_symlinks=False)) != signature:
                raise PreviewError("PREVIEW_SOURCE_CHANGED")
    return {"schema_version": 1, "folder_name": parts[-1], "missing": False, "ignored_entries": ignored, "items": items}


def _decode(fd):
    try:
        result = subprocess.run([sys.executable, "-m", "app.preview_decode", str(fd)], pass_fds=(fd,),
            cwd=Path(__file__).resolve().parent.parent, env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=25, check=False)
        if len(result.stdout) > MAX_WIRE_BYTES: raise ValueError()
        value = json.loads(result.stdout)
        if not isinstance(value, dict): raise ValueError()
        if "error" in value: raise PreviewError(value["error"])
        if (result.returncode != 0 or set(value) != {"source_sha256", "source_format", "width", "height", "media_type", "sha256", "data"}
                or value["source_format"] not in FORMATS or value["media_type"] != "image/jpeg"
                or any(type(value[key]) is not int or not 1 <= value[key] <= MAX_OUTPUT_SIDE for key in ("width", "height"))
                or any(not isinstance(value[key], str) or re.fullmatch(r"[a-f0-9]{64}", value[key]) is None for key in ("sha256", "source_sha256"))):
            raise ValueError()
        data = base64.b64decode(value["data"], validate=True)
        if not 0 < len(data) <= MAX_OUTPUT_BYTES or not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9") or hashlib.sha256(data).hexdigest() != value["sha256"]:
            raise ValueError()
        return value
    except subprocess.TimeoutExpired:
        raise PreviewError("PREVIEW_TIMEOUT") from None
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise PreviewError("PREVIEW_DECODER_FAILED") from None


def render_preview(root, parts, name, expected_sha256):
    if not valid_preview_name(name): raise PreviewError("PREVIEW_UNSAFE_NAME")
    if not isinstance(expected_sha256, str) or re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None:
        raise PreviewError("PREVIEW_SOURCE_CHANGED")
    return _bounded(_render_preview, root, parts, name, expected_sha256)


def _render_preview(root, parts, name, expected_sha256):
    with _preview_directory(root, parts) as directory_fd:
        if directory_fd is None: raise PreviewError("PREVIEW_NOT_FOUND")
        try: before = _regular_file(directory_fd, name, os.fstat(directory_fd).st_dev)
        except FileNotFoundError: raise PreviewError("PREVIEW_NOT_FOUND") from None
        fd = os.open(name, _metadata_flags(), dir_fd=directory_fd)
        try:
            _verify_file(directory_fd, name, fd, before)
            value = _decode(fd)
            _verify_file(directory_fd, name, fd, before)
            if value["source_sha256"] != expected_sha256: raise PreviewError("PREVIEW_SOURCE_CHANGED")
            if value["source_format"] != EXTENSIONS[name.rsplit(".", 1)[-1].lower()]:
                raise PreviewError("PREVIEW_EXTENSION_MISMATCH")
        finally: os.close(fd)
    return {"schema_version": 1, "folder_name": parts[-1], "name": name, **value}
