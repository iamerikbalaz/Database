"""Explicit local desktop export capability. No caller supplied filesystem paths.

Source handles remain read-only throughout packaging. The Linux packaging image
is injected by its immutable image ID and has no network access. Destination
capabilities come only from the desktop picker and are scoped to their actor.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from threading import Lock, RLock
from types import MappingProxyType
import time
from uuid import UUID, uuid4

from app.local_filesystem import LocalFilesystem, LocalFilesError
from app.material_review import canonical_hash


class LocalPublicationExport:
    def __init__(self, library, root, *, docker_executable, docker_context, image, original_master_observations=None):
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image): raise ValueError("An immutable packaging image is required")
        if docker_context != "desktop-linux": raise ValueError("Only the configured local Docker context is supported")
        executable = Path(docker_executable)
        if not executable.is_absolute() or not executable.is_file(): raise ValueError("A fixed Docker executable is required")
        self.library = library; self.root = Path(os.path.abspath(root))
        # Trusted copy/import provenance only, never HTTP input. Capture once
        # by stable material ID so subsequent renames cannot change ZIP policy.
        observations = {}
        for identifier, observed in (original_master_observations or {}).items():
            moment = datetime.fromisoformat(observed)
            if moment.tzinfo is None or moment.utcoffset() is None:
                raise ValueError("Original master observations must include a timezone")
            observations[str(UUID(str(identifier)))] = moment.astimezone(timezone.utc).isoformat()
        self.original_master_observations = MappingProxyType(observations)
        if self.root.is_relative_to(library.fs.root) or library.fs.root.is_relative_to(self.root):
            raise LocalFilesError("LOCAL_EXPORT_ROOT_OVERLAP")
        self.root.mkdir(exist_ok=True)
        self.fs = LocalFilesystem(self.root)
        self.docker = str(executable); self.context = docker_context; self.image = image
        self.tokens = {}; self.lock = RLock(); self.picker_lock = Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="publication-export")
        self.active = None

    def grant_destination(self, path, actor_id):
        """Internal/test seam. This method is never exposed as a path-taking API."""
        destination = LocalFilesystem(path)
        for protected in (self.library.fs.root, self.library.journal.root, self.root):
            # A parent such as Desktop is safe: output always goes into a new
            # unique sibling directory. Only writing inside a protected root
            # is forbidden; existing folders can never be replaced.
            if destination.root.is_relative_to(protected):
                raise LocalFilesError("LOCAL_EXPORT_DESTINATION_OVERLAP")
        with destination.directory(""), destination.opened(destination.root, directory=True) as handle:
            identity = destination.identity(handle)
        token = secrets.token_urlsafe(32)
        with self.lock:
            now = time.monotonic()
            self.tokens = {key: value for key, value in self.tokens.items() if value["expires"] > now}
            if len(self.tokens) >= 50: raise LocalFilesError("LOCAL_EXPORT_DESTINATION_LIMIT")
            self.tokens[token] = {"actor_id": str(actor_id), "path": str(destination.root), "identity": identity, "expires": now + 900}
        return {"destination_token": token, "destination_path": str(destination.root)}

    def select_destination(self, actor_id):
        if not self.picker_lock.acquire(False): raise LocalFilesError("LOCAL_PICKER_BUSY")
        try:
            script = "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); Add-Type -AssemblyName System.Windows.Forms; $d=New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description='Choose a folder for REAWOTE CSV and ZIP publication files'; $d.ShowNewFolderButton=$true; if($d.ShowDialog() -eq 'OK'){[Console]::Write($d.SelectedPath)}"
            result = subprocess.run([str(Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
                "-NoProfile", "-STA", "-Command", script], capture_output=True, text=True, encoding="utf-8", timeout=180, creationflags=0x08000000)
            if result.returncode: raise LocalFilesError("LOCAL_PICKER_UNAVAILABLE")
            path = result.stdout.strip()
            return self.grant_destination(path, actor_id) if path else {"destination_token": None, "destination_path": None}
        except subprocess.TimeoutExpired: raise LocalFilesError("LOCAL_PICKER_TIMEOUT") from None
        finally: self.picker_lock.release()

    def _read(self, identifier, filename="state.json"):
        return json.loads(self.fs.read(str(UUID(str(identifier))) + "/" + filename, limit=16 * 1024**2))

    def _write(self, identifier, value, filename="state.json"):
        with self.fs.directory(str(identifier)) as directory:
            temporary = directory / ("write-" + uuid4().hex + ".tmp")
            with temporary.open("xb") as target:
                target.write(json.dumps(value, ensure_ascii=True, indent=2).encode()); target.flush(); os.fsync(target.fileno())
            os.replace(temporary, directory / filename)

    def status(self, identifier, actor_id):
        with self.lock:
            try: value = self._read(identifier)
            except FileNotFoundError: raise LocalFilesError("LOCAL_EXPORT_NOT_FOUND") from None
            if value["actor_id"] != str(actor_id): raise LocalFilesError("LOCAL_EXPORT_NOT_FOUND")
            if value["status"] == "RUNNING" and self.active != str(identifier):
                # A restart cannot certify an interrupted job. Exact retries return
                # this terminal receipt and cannot silently rerun output writes.
                value.update(status="FAILED", error_code="LOCAL_EXPORT_INTERRUPTED")
                self._write(identifier, value)
            return value

    def start(self, actor_id, identifier, destination_token, request, verify):
        identifier = str(UUID(str(identifier)))
        digest = canonical_hash({"actor_id": str(actor_id), "destination_token": destination_token, "request": request})
        with self.lock:
            try:
                old = self.status(identifier, actor_id)
                if old["request_hash"] != digest: raise LocalFilesError("LOCAL_EXPORT_REQUEST_CONFLICT")
                return old
            except LocalFilesError as error:
                if error.code != "LOCAL_EXPORT_NOT_FOUND": raise
            if self.active is not None: raise LocalFilesError("LOCAL_EXPORT_BUSY")
            capability = self.tokens.get(destination_token)
            if (capability is None or capability["actor_id"] != str(actor_id) or capability["expires"] <= time.monotonic()):
                raise LocalFilesError("LOCAL_EXPORT_DESTINATION_EXPIRED")
            with self.fs.directory(""):
                directory = self.root / identifier; directory.mkdir()
                (directory / "files").mkdir(); (directory / "request").mkdir()
            value = {"id": identifier, "actor_id": str(actor_id), "request_hash": digest, "status": "RUNNING",
                "created_at": datetime.now(timezone.utc).isoformat(), "material_ids": [item["material_id"] for item in request["materials"]],
                "material_versions": request["material_versions"], "preview_hash": request["preview_hash"],
                "output_path": None, "csv_name": "materials.csv", "archives": [], "error_code": None, "issues": []}
            self._write(identifier, request, "frozen.json")
            self._write(identifier, {"schema_version": 1, "materials": request["materials"], "settings": request["settings"]}, "request/request.json")
            self._write(identifier, value)
            del self.tokens[destination_token]
            self.active = identifier
            self.executor.submit(self._execute, identifier, capability, request, verify)
            return value

    def _run_container(self, identifier):
        directory = self.root / identifier
        name = "reawote-local-export-" + UUID(identifier).hex
        command = [self.docker, "--context", self.context, "run", "--rm", "--name", name,
            "--label", "reawote.local-publication=" + identifier, "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "128", "--memory", "8g", "--cpus", "4",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=16g,mode=1777",
            "--mount", "type=bind,source=" + str(self.library.fs.root) + ",target=/materials,readonly",
            "--mount", "type=bind,source=" + str(directory / "request") + ",target=/request,readonly",
            "--mount", "type=bind,source=" + str(directory / "files") + ",target=/output",
            self.image, "python", "-m", "app.local_publication_export"]
        try:
            result = subprocess.run(command, capture_output=True, timeout=7200, creationflags=0x08000000)
        except subprocess.TimeoutExpired:
            subprocess.run([self.docker, "--context", self.context, "rm", "-f", name], capture_output=True, timeout=30, creationflags=0x08000000)
            raise LocalFilesError("LOCAL_EXPORT_TIME_LIMIT") from None
        if result.returncode: raise LocalFilesError("LOCAL_EXPORT_PACKAGING_UNAVAILABLE")

    def _execute(self, identifier, capability, request, verify):
        state = self._read(identifier)
        try:
            with verify() or nullcontext(): pass
            # Native handles, not the library's Python mutex, freeze sources.
            # Never acquire database locks while holding that mutex: metadata
            # readers legitimately acquire their database locks first.
            with ExitStack() as held:
                for item in request["materials"]:
                    held.enter_context(self.library.fs.tree(item["folder_path"], hash_files=False, for_rename=False))
                self._run_container(identifier)
                result = self._read(identifier, "files/result.json")
                if result["status"] != "COMPLETED":
                    state["issues"] = [{"material_id": item["material_id"], "issues": item["issues"]} for item in result.get("items", []) if item.get("issues")]
                    raise LocalFilesError(result.get("error_code", "LOCAL_EXPORT_MATERIAL_FILES_INVALID"))
                if sorted(item["material_id"] for item in result["items"]) != sorted(state["material_ids"]):
                    raise LocalFilesError("LOCAL_EXPORT_ARTIFACT_INVALID")
                state["archives"] = [archive for item in result["items"] for archive in item["archives"]]
                state["packaging"] = result["items"]
                state["output_path"] = self._publish(identifier, capability, request, state, verify)
            state.update(status="COMPLETED", finished_at=datetime.now(timezone.utc).isoformat())
        except Exception as error:
            code = getattr(error, "code", "LOCAL_EXPORT_FAILED")
            state.update(status="FAILED", error_code=code, finished_at=datetime.now(timezone.utc).isoformat())
        finally:
            self._write(identifier, state)
            with self.lock: self.active = None

    def _publish(self, identifier, capability, request, state, verify):
        destination = LocalFilesystem(capability["path"])
        name = "REAWOTE-publication-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + identifier[:8]
        partial = "REAWOTE-incomplete-" + identifier
        with destination.directory(""), destination.opened(destination.root, directory=True) as root_handle:
            if destination.identity(root_handle) != capability["identity"]: raise LocalFilesError("LOCAL_EXPORT_DESTINATION_CHANGED")
            staging = destination.root / partial; staging.mkdir()
            with destination.directory(partial):
                seen = set()
                for archive in state["archives"]:
                    filename = archive["name"]
                    if (not re.fullmatch(r"[A-Za-z0-9_.-]+\.zip", filename) or filename.casefold() in seen
                            or type(archive["size"]) is not int or not 0 < archive["size"] <= 64 * 1024**3
                            or not re.fullmatch("[a-f0-9]{64}", archive["sha256"])):
                        raise LocalFilesError("LOCAL_EXPORT_ARTIFACT_INVALID")
                    seen.add(filename.casefold()); digest = hashlib.sha256(); size = 0
                    source = self.root / identifier / "files" / filename
                    with self.fs.directory(identifier + "/files"), self.fs.opened(source), source.open("rb") as incoming, (staging / filename).open("xb") as outgoing:
                        while chunk := incoming.read(1024**2):
                            size += len(chunk)
                            if size > archive["size"]: raise LocalFilesError("LOCAL_EXPORT_ARTIFACT_CHANGED")
                            digest.update(chunk); outgoing.write(chunk)
                        outgoing.flush(); os.fsync(outgoing.fileno())
                    if size != archive["size"] or digest.hexdigest() != archive["sha256"]: raise LocalFilesError("LOCAL_EXPORT_ARTIFACT_CHANGED")
                csv = bytes.fromhex(request["csv_hex"])
                with (staging / "materials.csv").open("xb") as target:
                    target.write(csv); target.flush(); os.fsync(target.fileno())
                receipt = {"id": identifier, "created_at": state["created_at"], "csv_sha256": hashlib.sha256(csv).hexdigest(),
                    "material_ids": state["material_ids"], "archives": state["archives"], "packaging_settings": request["settings"],
                    "upload_performed": False}
                with (staging / "export-receipt.json").open("x", encoding="utf-8") as target:
                    json.dump(receipt, target, ensure_ascii=True, indent=2); target.flush(); os.fsync(target.fileno())
            # The API's guard retains current authorization and all selected DB
            # row locks across the final atomic rename, after expensive copying.
            with verify() or nullcontext(), destination.opened(staging, directory=True, rename=True) as handle:
                destination.rename_handle(handle, destination.root / name)
        return str(destination.root / name)

    def frozen(self, identifier, actor_id):
        self.status(identifier, actor_id)
        return self._read(identifier, "frozen.json")

    @contextmanager
    def unchanged_sources(self, identifier, actor_id):
        """Bind manual Published confirmation to the exact exported file set."""
        state = self.status(identifier, actor_id)
        frozen = self.frozen(identifier, actor_id)
        proofs = {item["material_id"]: item["source_entries"] for item in state["packaging"]}
        with ExitStack() as held:
            for item in frozen["materials"]:
                _, entries, _ = held.enter_context(self.library.fs.tree(item["folder_path"], for_rename=False))
                if entries != proofs.get(item["material_id"]): raise LocalFilesError("LOCAL_EXPORT_SOURCE_CHANGED")
                # Re-enumeration detects added entries during the full hash pass.
                with self.library.fs.tree(item["folder_path"], hash_files=False, for_rename=False) as (_, current, _):
                    stripped = [{**entry, "sha256": None} for entry in entries]
                    if current != stripped: raise LocalFilesError("LOCAL_EXPORT_SOURCE_CHANGED")
            yield
