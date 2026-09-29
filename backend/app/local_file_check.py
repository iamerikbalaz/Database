"""Read-only native bridge to the isolated, complete material file checker.

The application injects a fixed local Docker executable and immutable image.
HTTP callers can select only already-authorized relative material folders.
One container handles a whole selection while native handles pin its sources.
"""
from contextlib import ExitStack
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from threading import Lock
import time
from uuid import uuid4

from app.local_filesystem import LocalFilesystem, LocalFilesError


PROFILE = "PBR_FILES_V1"
MAX_RESULT_BYTES = 32 * 1024**2
MAX_PROGRESS_BYTES = 16 * 1024
PROGRESS_PHASES = {"STAGING", "CHECKING", "VERIFYING", "CACHED", "QUEUED", "HASHING", "DECODING"}


def _protect_private_directory(path):
    """Restrict signing-key inheritance to this Windows user and SYSTEM."""
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    token = wintypes.HANDLE(); sid_text = wintypes.LPWSTR(); descriptor = ctypes.c_void_p()
    try:
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise LocalFilesError("LOCAL_FILE_CHECK_KEY_UNAVAILABLE")
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        data = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, data, len(data), ctypes.byref(size)):
            raise LocalFilesError("LOCAL_FILE_CHECK_KEY_UNAVAILABLE")
        sid = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0]
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise LocalFilesError("LOCAL_FILE_CHECK_KEY_UNAVAILABLE")
        sddl = "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;" + sid_text.value + ")"
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise LocalFilesError("LOCAL_FILE_CHECK_KEY_UNAVAILABLE")
        if not advapi.SetFileSecurityW(str(path), 0x80000004, descriptor):
            raise LocalFilesError("LOCAL_FILE_CHECK_KEY_UNAVAILABLE")
    finally:
        if token: kernel.CloseHandle(token)
        if sid_text: kernel.LocalFree(sid_text)
        if descriptor: kernel.LocalFree(descriptor)


def _parse_progress(raw, identifiers):
    """Progress is advisory; it can never manufacture a completed result."""
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
        keys = {"schema_version", "total", "completed", "active", "cache_hits", "cache_misses"}
        if (type(value) is not dict or set(value) != keys or type(value["schema_version"]) is not int
                or value["schema_version"] != 2 or type(value["total"]) is not int or value["total"] != len(identifiers)
                or type(value["completed"]) is not int or not 0 <= value["completed"] <= len(identifiers)
                or type(value["active"]) is not list or len(value["active"]) > 2
                or any(type(value[key]) is not int or not 0 <= value[key] <= 100_000 for key in ("cache_hits", "cache_misses"))):
            return None
        indices = set()
        for item in value["active"]:
            if (type(item) is not dict or set(item) != {"material_id", "material_index", "file", "phase"}
                    or type(item["material_index"]) is not int or not 1 <= item["material_index"] <= len(identifiers)
                    or item["material_id"] != identifiers[item["material_index"] - 1]
                    or item["material_index"] in indices or item["phase"] not in PROGRESS_PHASES
                    or type(item["file"]) is not str or len(item["file"].encode("utf-8")) > 2048
                    or any(ord(char) < 32 or ord(char) == 127 or char in "\\:" for char in item["file"])
                    or (item["file"] and any(part in {"", ".", ".."} for part in item["file"].split("/")))):
                return None
            indices.add(item["material_index"])
        if value["completed"] + len(indices) > len(identifiers):
            return None
        return value
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        return None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _parse_results(raw, identifiers):
    """Never certify a partial, ambiguous, or differently-versioned response."""
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
        if (type(value) is not dict or set(value) != {"schema_version", "results"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or type(value["results"]) is not list or len(value["results"]) != len(identifiers)):
            raise ValueError()
        by_id = {}
        for item in value["results"]:
            if (type(item) is not dict or set(item) != {"id", "profile", "complete", "issues", "report"}
                    or type(item["id"]) is not str or item["id"] not in identifiers or item["id"] in by_id
                    or item["profile"] != PROFILE or item["complete"] is not True
                    or type(item["report"]) is not str or not item["report"] or len(item["report"]) > 1_000_000
                    or type(item["issues"]) is not list or len(item["issues"]) > 4096
                    or any(type(issue) is not str or not issue or len(issue) > 4096 for issue in item["issues"])):
                raise ValueError()
            by_id[item["id"]] = {key: item[key] for key in ("profile", "complete", "issues", "report")}
        return [by_id[identifier] for identifier in identifiers]
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
        raise LocalFilesError("LOCAL_FILE_CHECK_RESULT_INVALID") from None


class LocalMaterialFileCheck:
    profile = PROFILE

    def __init__(self, library, root, *, docker_executable, docker_context, image):
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
            raise ValueError("An immutable file-check image is required")
        if docker_context != "desktop-linux":
            raise ValueError("Only the configured local Docker context is supported")
        executable = Path(docker_executable)
        if not executable.is_absolute() or not executable.is_file():
            raise ValueError("A fixed Docker executable is required")
        self.library = library
        self.root = Path(os.path.abspath(root))
        for protected in (library.fs.root, library.journal.root):
            if self.root.is_relative_to(protected) or protected.is_relative_to(self.root):
                raise LocalFilesError("LOCAL_FILE_CHECK_ROOT_OVERLAP")
        if any("," in str(path) for path in (self.root, library.fs.root)):
            raise LocalFilesError("LOCAL_FILE_CHECK_MOUNT_INVALID")
        parent = LocalFilesystem(self.root.parent)
        with parent.directory(""):
            self.root.mkdir(exist_ok=True)
        self.fs = LocalFilesystem(self.root)
        self.docker = str(executable)
        self.context = docker_context
        self.image = image
        self.lock = Lock()
        with self.fs.directory(""):
            (self.root / "cache").mkdir(exist_ok=True)
            (self.root / "cache-key").mkdir(exist_ok=True)
        with self.fs.directory("cache"), self.fs.directory("cache-key") as key_directory:
            _protect_private_directory(key_directory)
            key_path = key_directory / "private.key"
            try:
                with key_path.open("xb") as target:
                    target.write(secrets.token_bytes(32)); target.flush(); os.fsync(target.fileno())
            except FileExistsError:
                pass
            # Verify the existing file before modifying its ACL; never follow
            # a substituted link out of the explicitly owned private root.
            with self.fs.opened(key_path):
                _protect_private_directory(key_path)
                if len(self.fs.read("cache-key/private.key", limit=32)) != 32:
                    raise LocalFilesError("LOCAL_FILE_CHECK_KEY_INVALID")

    def _stop_container(self, name):
        try:
            subprocess.run([self.docker, "--context", self.context, "rm", "-f", "-v", name],
                capture_output=True, timeout=30, creationflags=0x08000000)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _run_container(self, identifier, *, progress=None):
        directory = self.root / identifier
        name = "reawote-local-check-" + identifier
        command = [self.docker, "--context", self.context, "run", "--rm", "--name", name,
            "--label", "reawote.local-file-check=" + identifier,
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "128", "--memory", "6g", "--cpus", "2",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m,mode=1777",
            "--mount", "type=volume,target=/staging",
            "--mount", "type=bind,source=" + str(self.library.fs.root) + ",target=/materials,readonly",
            "--mount", "type=bind,source=" + str(directory / "request") + ",target=/request,readonly",
            "--mount", "type=bind,source=" + str(directory / "output") + ",target=/output",
            "--mount", "type=bind,source=" + str(self.root / "cache") + ",target=/cache",
            "--mount", "type=bind,source=" + str(self.root / "cache-key") + ",target=/cache-key,readonly",
            "--env", "REAWOTE_CHECK_WORKERS=2", "--env", "REAWOTE_CHECK_CACHE_NAMESPACE=" + self.image,
            self.image, "python", "-m", "app.local_file_check"]
        process = None
        completed = False
        try:
            if progress is None:
                result = subprocess.run(command, capture_output=True, timeout=7200, creationflags=0x08000000)
            else:
                # Polling happens on the check job's worker thread. No decoder
                # stdout or stderr is forwarded to the HTTP progress response.
                process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=0x08000000)
                deadline = time.monotonic() + 7200
                try:
                    while True:
                        progress()
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(command, 7200)
                        try:
                            process.wait(timeout=min(.5, remaining))
                            progress()
                            result = process
                            break
                        except subprocess.TimeoutExpired:
                            if time.monotonic() >= deadline: raise
                finally:
                    if process.poll() is None:
                        try:
                            process.terminate()
                            try: process.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                process.kill(); process.wait(timeout=5)
                        except (OSError, subprocess.TimeoutExpired):
                            pass
            completed = True
        except subprocess.TimeoutExpired:
            if process is None:
                self._stop_container(name)
            raise LocalFilesError("LOCAL_FILE_CHECK_TIME_LIMIT") from None
        except OSError:
            raise LocalFilesError("LOCAL_FILE_CHECK_UNAVAILABLE") from None
        finally:
            # --rm normally cleans anonymous volumes. Explicit -v also handles
            # client/wait failures after Docker already created the container.
            if not completed and process is not None:
                self._stop_container(name)
        if result.returncode:
            raise LocalFilesError("LOCAL_FILE_CHECK_FAILED")

    def check_many(self, folders, *, progress=None):
        if (type(folders) is not list or not 1 <= len(folders) <= 100
                or any(type(folder) is not str or not folder for folder in folders)
                or len({folder.casefold() for folder in folders}) != len(folders)):
            raise LocalFilesError("LOCAL_FILE_CHECK_SELECTION_INVALID")
        # Validate every path before creating a job or launching a subprocess.
        for folder in folders:
            self.library.fs.path(folder)
        if not self.lock.acquire(False):
            raise LocalFilesError("LOCAL_FILE_CHECK_BUSY")
        try:
            identifier = uuid4().hex
            identifiers = [str(uuid4()) for _ in folders]
            request = {"schema_version": 1, "materials": [
                {"id": item_id, "folder_path": folder} for item_id, folder in zip(identifiers, folders)]}
            with self.fs.directory(""):
                directory = self.root / identifier
                directory.mkdir()
                (directory / "request").mkdir()
                (directory / "output").mkdir()
            with self.fs.directory(identifier + "/request") as request_directory:
                with (request_directory / "request.json").open("xb") as target:
                    target.write(json.dumps(request, ensure_ascii=True).encode("utf-8"))
                    target.flush()
                    os.fsync(target.fileno())
            # Do not hold the library Python mutex during expensive decoding:
            # source handles provide the protection without DB/mutex inversion.
            with ExitStack() as held:
                snapshots = [held.enter_context(self.library.fs.tree(folder, hash_files=False, for_rename=False))[1]
                    for folder in folders]
                held.enter_context(self.fs.directory(identifier + "/request"))
                held.enter_context(self.fs.directory(identifier + "/output"))
                held.enter_context(self.fs.directory("cache"))
                held.enter_context(self.fs.directory("cache-key"))
                held.enter_context(self.fs.opened(self.root / "cache-key/private.key"))
                if progress is None:
                    self._run_container(identifier)
                else:
                    last_progress = None
                    def read_progress():
                        nonlocal last_progress
                        try:
                            raw = self.fs.read(identifier + "/output/progress.json", limit=MAX_PROGRESS_BYTES)
                            value = _parse_progress(raw, identifiers)
                        except (OSError, ValueError, LocalFilesError):
                            return
                        if value is None or value == last_progress:
                            return
                        if last_progress is not None and any(value[key] < last_progress[key]
                                for key in ("completed", "cache_hits", "cache_misses")):
                            return
                        last_progress = value
                        # The observer cannot alter certification or abort the
                        # read-only check merely because its UI disconnected.
                        try: progress(value)
                        except Exception: pass
                    self._run_container(identifier, progress=read_progress)
                raw = self.fs.read(identifier + "/output/result.json", limit=MAX_RESULT_BYTES)
                results = _parse_results(raw, identifiers)
                for folder, before, result in zip(folders, snapshots, results):
                    with self.library.fs.tree(folder, hash_files=False, for_rename=False) as (path, after, _):
                        if before != after:
                            raise LocalFilesError("LOCAL_SOURCE_CHANGED")
                        result["report"] = "Folder: " + str(path) + "\n\n" + result["report"]
                        if len(result["report"]) > 1_000_000:
                            raise LocalFilesError("LOCAL_FILE_CHECK_RESULT_INVALID")
                return results
        except (OSError, ValueError):
            raise LocalFilesError("LOCAL_FILE_CHECK_UNAVAILABLE") from None
        finally:
            self.lock.release()

    def check(self, folder, *, progress=None):
        return self.check_many([folder], progress=progress)[0]
