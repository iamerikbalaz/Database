"""Bounded desktop check jobs; every poll reauthorizes the complete selection.

Jobs are local to this application process, retained for one hour. The client
resumes a known job instead of starting another expensive scan after a lost reply.
Only complete checks commit results through the existing atomic service.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Lock, Thread
import time
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.auth.access import ApplicationAccess, MATERIAL_EDITORS
from app.automatic_file_check import CheckSelection, _context, check_materials, save_combined_report
from app.material_review import canonical_hash

PHASES = frozenset({"STAGING", "CHECKING", "VERIFYING", "CACHED", "QUEUED", "HASHING", "DECODING"})
MAX_JOBS = 8
RETENTION_SECONDS = 3600
KEY_RETENTION_SECONDS = 24 * 3600
MAX_REQUEST_KEYS = 1024


@dataclass
class FileCheckJob:
    id: str
    key: str
    owner: UUID
    request_hash: str
    selections: list
    identities: list[str]
    open_report: bool
    started: float = field(default_factory=time.monotonic)
    finished: float | None = None
    status: str = "RUNNING"
    completed: int = 0
    active: list = field(default_factory=list)
    cache_hits: int = 0
    cache_misses: int = 0
    result: dict | None = None
    error: dict | None = None


class FileCheckJobs:
    def __init__(self, database, library):
        self.database = database
        self.library = library
        self.lock = Lock()
        self.jobs: dict[str, FileCheckJob] = {}
        # Compact tombstones survive result eviction. A retry of a failed or
        # evicted job must not silently launch the expensive scan again.
        self.request_keys: dict[tuple[UUID, str], float] = {}

    def _authorize(self, access, selections, *, check_versions):
        identities = {}
        with self.database.session() as session:
            access.check(session, MATERIAL_EDITORS)
            for selected in sorted(selections, key=lambda item: str(item.id)):
                selection = selected if check_versions else CheckSelection(id=selected.id)
                material, _ = _context(session, selection, access)
                identities[selected.id] = material.technical_identity
        return [identities[item.id] for item in selections]

    def _prune(self):
        now = time.monotonic()
        self.request_keys = {key: expiry for key, expiry in self.request_keys.items() if expiry > now}
        if len(self.request_keys) >= MAX_REQUEST_KEYS:
            raise HTTPException(429, {"code": "FILE_CHECK_CAPACITY"})
        for identifier, job in list(self.jobs.items()):
            if job.finished is not None and now - job.finished > RETENTION_SECONDS:
                del self.jobs[identifier]
        while len(self.jobs) >= MAX_JOBS:
            terminal = [job for job in self.jobs.values() if job.finished is not None]
            if not terminal:
                raise HTTPException(409, {"code": "FILE_CHECK_BUSY"})
            del self.jobs[min(terminal, key=lambda job: job.finished).id]

    @staticmethod
    def _snapshot(job):
        return {"id": job.id, "status": job.status, "total": len(job.selections),
            "completed": job.completed, "active": deepcopy(job.active),
            "elapsed_seconds": round((job.finished or time.monotonic()) - job.started, 1),
            "cache_hits": job.cache_hits, "cache_misses": job.cache_misses,
            "result": deepcopy(job.result), "error": deepcopy(job.error)}

    def start(self, access, payload, key: UUID):
        if self.library is None:
            raise HTTPException(503, {"code": "LOCAL_DESKTOP_UNAVAILABLE"})
        digest = canonical_hash(payload.model_dump(mode="json"))
        # Look up retries before comparing old versions: a completed check itself
        # changes updated_at. Current scope is nevertheless checked on every retry.
        with self.lock:
            existing = next((job for job in self.jobs.values()
                if job.owner == access.user.id and job.key == str(key)), None)
            consumed = self.request_keys.get((access.user.id, str(key)), 0) > time.monotonic()
        if existing is not None:
            self._authorize(access, existing.selections, check_versions=False)
            if existing.finished is not None and time.monotonic() - existing.finished > RETENTION_SECONDS:
                raise HTTPException(404, {"code": "FILE_CHECK_JOB_NOT_FOUND"})
            if existing.request_hash != digest:
                raise HTTPException(409, {"code": "FILE_CHECK_REQUEST_CONFLICT"})
            with self.lock:
                return self._snapshot(existing)
        if consumed:
            raise HTTPException(404, {"code": "FILE_CHECK_JOB_NOT_FOUND"})
        identities = self._authorize(access, payload.materials, check_versions=True)
        with self.lock:
            # Another start may have raced the authorization transaction.
            existing = next((job for job in self.jobs.values()
                if job.owner == access.user.id and job.key == str(key)), None)
            if existing is not None:
                if existing.request_hash != digest:
                    raise HTTPException(409, {"code": "FILE_CHECK_REQUEST_CONFLICT"})
                return self._snapshot(existing)
            if self.request_keys.get((access.user.id, str(key)), 0) > time.monotonic():
                raise HTTPException(404, {"code": "FILE_CHECK_JOB_NOT_FOUND"})
            if any(job.status == "RUNNING" for job in self.jobs.values()):
                raise HTTPException(409, {"code": "FILE_CHECK_BUSY"})
            self._prune()
            job = FileCheckJob(str(uuid4()), str(key), access.user.id, digest,
                deepcopy(payload.materials), identities, payload.open_report)
            self.jobs[job.id] = job
            self.request_keys[(job.owner, job.key)] = time.monotonic() + KEY_RETENTION_SECONDS
            initial = self._snapshot(job)
        try:
            self._launch(job, ApplicationAccess(access.context))
        except Exception:
            with self.lock:
                job.status = "FAILED"; job.finished = time.monotonic()
                job.error = {"code": "FILE_CHECK_START_FAILED", "message": "The file check could not start."}
            raise HTTPException(503, {"code": "FILE_CHECK_START_FAILED"}) from None
        return initial

    def get(self, access, identifier: UUID):
        with self.lock:
            job = self.jobs.get(str(identifier))
            if (job is None or job.owner != access.user.id
                    or (job.finished is not None and time.monotonic() - job.finished > RETENTION_SECONDS)):
                raise HTTPException(404, {"code": "FILE_CHECK_JOB_NOT_FOUND"})
        self._authorize(access, job.selections, check_versions=False)
        with self.lock:
            return self._snapshot(job)

    def _launch(self, job, access):
        Thread(target=self._execute, args=(job, access), daemon=True,
            name="material-check-" + job.id).start()

    def _progress(self, job, value):
        # Defense in depth: progress is display-only and never certifies an OK.
        try:
            total = len(job.selections)
            if (type(value) is not dict or value.get("schema_version") != 2
                    or type(value.get("total")) is not int or value["total"] != total
                    or type(value.get("completed")) is not int or not 0 <= value["completed"] <= total
                    or type(value.get("active")) is not list or len(value["active"]) > 2):
                return
            counters = [value.get(key) for key in ("cache_hits", "cache_misses")]
            if any(type(count) is not int or not 0 <= count <= 100_000 for count in counters):
                return
            active = []; seen = set()
            for item in value["active"]:
                index, file, phase = item["material_index"], item.get("file"), item["phase"]
                if file == "": file = None
                if type(index) is not int or not 1 <= index <= total or index in seen or phase not in PHASES:
                    return
                if file is not None and (type(file) is not str or len(file) > 2048 or not file
                        or any(ord(char) < 32 or char in "\\:" for char in file)
                        or any(part in {"", ".", ".."} for part in file.split("/"))):
                    return
                seen.add(index)
                active.append({"material_id": str(job.selections[index-1].id),
                    "identity": job.identities[index-1], "file": file, "phase": phase})
            with self.lock:
                if job.status != "RUNNING": return
                job.completed = max(job.completed, value["completed"])
                job.active = active
                job.cache_hits = max(job.cache_hits, counters[0])
                job.cache_misses = max(job.cache_misses, counters[1])
        except (KeyError, TypeError, ValueError):
            return

    def _execute(self, job, access):
        try:
            items = check_materials(self.database, self.library, access, job.selections,
                progress=lambda value: self._progress(job, value))
            result = save_combined_report(self.database, self.library, access, items, open_report=job.open_report)
            with self.lock:
                job.result = result
                job.completed = len(job.selections)
                job.active = []
                job.status = "COMPLETED"
                job.finished = time.monotonic()
        except Exception as error:
            code = "FILE_CHECK_FAILED"
            message = "The file check could not finish. Reload the materials and check the local connection."
            if isinstance(error, HTTPException):
                detail = error.detail if isinstance(error.detail, dict) else {}
                if detail.get("code") == "LOCAL_MATERIAL_CHANGED":
                    code = "LOCAL_MATERIAL_CHANGED"
                    message = "A selected material changed during the check. Reload before checking it again."
                elif error.status_code in {401, 403, 404}:
                    code = "FILE_CHECK_ACCESS_CHANGED"
                    message = "The session or access to the selected materials changed during the check."
            with self.lock:
                job.status = "FAILED"; job.active = []
                job.error = {"code": code, "message": message}
                job.finished = time.monotonic()
