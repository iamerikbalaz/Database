"""Explicitly injected Windows filesystem capability for an owned local library.

No HTTP caller chooses the root. Ancestors and regular files are opened without
delete sharing, reparse points/hardlinks are refused, and renames use the already
verified handle with replacement disabled. This is separate from the NAS worker.
"""
from contextlib import contextmanager, ExitStack
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import stat

from app.discovery_client import validate_parent_path


class LocalFilesError(RuntimeError):
    def __init__(self, code="LOCAL_FILES_UNAVAILABLE"):
        self.code = code
        super().__init__(code)


class FileInfo(ctypes.Structure):
    _fields_ = [("attrs", wintypes.DWORD), ("creation", wintypes.FILETIME),
                ("access", wintypes.FILETIME), ("write", wintypes.FILETIME),
                ("volume", wintypes.DWORD), ("sizehi", wintypes.DWORD),
                ("sizelo", wintypes.DWORD), ("links", wintypes.DWORD),
                ("indexhi", wintypes.DWORD), ("indexlo", wintypes.DWORD)]


class RenameInfo(ctypes.Structure):
    _fields_ = [("replace", wintypes.BOOLEAN), ("root", wintypes.HANDLE),
                ("length", wintypes.DWORD), ("name", wintypes.WCHAR * 1)]


def signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class LocalFilesystem:
    def __init__(self, root):
        if os.name != "nt": raise LocalFilesError("LOCAL_FILES_WINDOWS_REQUIRED")
        self.root = Path(os.path.abspath(root))
        if not self.root.is_absolute() or self.root == Path(self.root.anchor):
            raise LocalFilesError("LOCAL_ROOT_INVALID")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self.kernel.CreateFileW.restype = wintypes.HANDLE
        self.kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInfo)]
        self.kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        self.kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        self.kernel.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        with self.directory(""): pass

    def path(self, relative):
        parts = validate_parent_path(relative)
        path = self.root.joinpath(*parts)
        if not path.is_relative_to(self.root): raise LocalFilesError("LOCAL_PATH_UNSAFE")
        return path

    def relative(self, absolute):
        path = Path(os.path.abspath(absolute))
        try: relative = path.relative_to(self.root).as_posix()
        except ValueError: raise LocalFilesError("LOCAL_DESTINATION_OUTSIDE_LIBRARY") from None
        relative = "" if relative == "." else relative
        self.path(relative)
        return relative

    @contextmanager
    def opened(self, path, *, directory=False, rename=False):
        access = (0x81 if directory else 0x80000000) | (0x10000 if rename else 0)
        flags = 0x00200000 | (0x02000000 if directory else 0)
        handle = self.kernel.CreateFileW(str(path), access, 3 if directory else 1, None, 3, flags, None)
        if handle == ctypes.c_void_p(-1).value:
            error = ctypes.get_last_error()
            if error in {2, 3}: raise FileNotFoundError(str(path))
            raise LocalFilesError("LOCAL_FILE_BUSY")
        try:
            info = FileInfo()
            if not self.kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
                raise LocalFilesError("LOCAL_FILE_UNVERIFIED")
            if bool(info.attrs & 0x10) != directory or info.attrs & 0x400 or (not directory and info.links != 1):
                raise LocalFilesError("LOCAL_PATH_UNSAFE")
            final = ctypes.create_unicode_buffer(32768)
            length = self.kernel.GetFinalPathNameByHandleW(handle, final, len(final), 0)
            expected = "\\\\?\\" + str(path)
            if length < 1 or length >= len(final) or final.value.rstrip("\\").casefold() != expected.rstrip("\\").casefold():
                raise LocalFilesError("LOCAL_PATH_UNSAFE")
            yield handle
        finally: self.kernel.CloseHandle(handle)

    @contextmanager
    def directory(self, relative):
        path = self.path(relative)
        with ExitStack() as stack:
            current = Path(path.anchor)
            stack.enter_context(self.opened(current, directory=True))
            for part in path.parts[1:]:
                current /= part
                stack.enter_context(self.opened(current, directory=True))
            yield path

    def read_handle(self, handle, *, limit=64 * 1024**2, digest_only=False):
        if not self.kernel.SetFilePointerEx(handle, 0, None, 0): raise LocalFilesError()
        chunks = []; total = 0; digest = hashlib.sha256()
        buffer = ctypes.create_string_buffer(1024 * 1024)
        while True:
            count = wintypes.DWORD()
            if not self.kernel.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None): raise LocalFilesError()
            if not count.value: break
            total += count.value
            if total > limit: raise LocalFilesError("LOCAL_FILE_TOO_LARGE")
            data = buffer.raw[:count.value]; digest.update(data)
            if not digest_only: chunks.append(data)
        return digest.hexdigest() if digest_only else b"".join(chunks)

    def rename_handle(self, handle, destination):
        # Windows rename is atomic, and refuses an existing destination.
        name = str(destination).encode("utf-16-le")
        # FileNameLength excludes the UTF-16 terminator, but the Win32 wrapper
        # still requires a terminated FileName buffer (not just NT length data).
        memory = ctypes.create_string_buffer(RenameInfo.name.offset + len(name) + 2)
        info = ctypes.cast(memory, ctypes.POINTER(RenameInfo)).contents
        info.replace = False; info.root = None; info.length = len(name)
        ctypes.memmove(ctypes.addressof(memory) + RenameInfo.name.offset, name, len(name))
        if not self.kernel.SetFileInformationByHandle(handle, 3, memory, len(memory)):
            raise LocalFilesError("LOCAL_RENAME_FAILED")

    def identity(self, handle):
        info = FileInfo()
        if not self.kernel.GetFileInformationByHandle(handle, ctypes.byref(info)): raise LocalFilesError()
        return [info.volume, info.indexhi, info.indexlo, bool(info.attrs & 0x10)]

    def read(self, relative, *, limit=4 * 1024**2):
        path = self.path(relative)
        with self.directory("" if path.parent == self.root else path.parent.relative_to(self.root).as_posix()):
            with self.opened(path) as handle: return self.read_handle(handle, limit=limit)

    @contextmanager
    def tree(self, relative):
        """Hold every source entry stable until the planned rename is complete."""
        path = self.path(relative)
        if path == self.root: raise LocalFilesError("LOCAL_PATH_UNSAFE")
        parent = path.parent.relative_to(self.root).as_posix()
        with self.directory("" if parent == "." else parent), ExitStack() as stack:
            handles = {}; entries = []
            handles[""] = stack.enter_context(self.opened(path, directory=True, rename=True))
            pending = [(path, "")]
            while pending:
                directory, prefix = pending.pop()
                with os.scandir(directory) as iterator:
                    for entry in iterator:
                        if len(entries) >= 4096: raise LocalFilesError("LOCAL_ENTRY_LIMIT")
                        sub = prefix + "/" + entry.name if prefix else entry.name
                        validate_parent_path(sub)
                        info = entry.stat(follow_symlinks=False)
                        if getattr(info, "st_file_attributes", 0) & 0x400 or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                            raise LocalFilesError("LOCAL_PATH_UNSAFE")
                        is_dir = stat.S_ISDIR(info.st_mode)
                        handle = stack.enter_context(self.opened(Path(entry.path), directory=is_dir, rename=True))
                        handles[sub] = handle
                        digest = None if is_dir else self.read_handle(handle, limit=32 * 1024**3, digest_only=True)
                        entries.append({"path": sub, "kind": "directory" if is_dir else "file", "size": 0 if is_dir else info.st_size,
                                        "sha256": digest})
                        if is_dir: pending.append((Path(entry.path), sub))
            yield path, sorted(entries, key=lambda item: item["path"]), handles
