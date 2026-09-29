"""Read-only native bridge to the isolated, complete material file checker.

The application injects a fixed local Docker executable and immutable image.
HTTP callers can select only already-authorized relative material folders.
One container handles a whole selection while native handles pin its sources.
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import subprocess
from threading import Lock
from uuid import uuid4

from app.local_filesystem import LocalFilesystem, LocalFilesError


PROFILE = "PBR_FILES_V1"
MAX_RESULT_BYTES = 32 * 1024**2


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

    def _run_container(self, identifier):
        directory = self.root / identifier
        name = "reawote-local-check-" + identifier
        command = [self.docker, "--context", self.context, "run", "--rm", "--name", name,
            "--label", "reawote.local-file-check=" + identifier,
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "128", "--memory", "4g", "--cpus", "2",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m,mode=1777",
            "--mount", "type=bind,source=" + str(self.library.fs.root) + ",target=/materials,readonly",
            "--mount", "type=bind,source=" + str(directory / "request") + ",target=/request,readonly",
            "--mount", "type=bind,source=" + str(directory / "output") + ",target=/output",
            self.image, "python", "-m", "app.local_file_check"]
        try:
            result = subprocess.run(command, capture_output=True, timeout=7200, creationflags=0x08000000)
        except subprocess.TimeoutExpired:
            try:
                subprocess.run([self.docker, "--context", self.context, "rm", "-f", name],
                    capture_output=True, timeout=30, creationflags=0x08000000)
            except (OSError, subprocess.TimeoutExpired):
                pass
            raise LocalFilesError("LOCAL_FILE_CHECK_TIME_LIMIT") from None
        except OSError:
            raise LocalFilesError("LOCAL_FILE_CHECK_UNAVAILABLE") from None
        if result.returncode:
            raise LocalFilesError("LOCAL_FILE_CHECK_FAILED")

    def check_many(self, folders):
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
                self._run_container(identifier)
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

    def check(self, folder):
        return self.check_many([folder])[0]
