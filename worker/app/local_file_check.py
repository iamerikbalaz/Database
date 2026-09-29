"""One-shot full desktop checker. Source mount is read-only and network-free."""
import json
import multiprocessing
import os
from pathlib import Path
from queue import Empty
import time
from uuid import UUID

from app.check_execution import ExecutionOptions, MaterialExecution
from app.inventory import InventoryError, _safe_name
from app.material_file_check import check_material_files
from app.secure_filesystem import (MaterialFolderNotFound, MaterialsRootUnavailable,
    SecureFilesystemAccessUnavailable, UnsafeMaterialPath)


SAFE_INVENTORY_ERRORS = frozenset({"INVENTORY_TIME_LIMIT", "INVENTORY_DEPTH_LIMIT", "INVENTORY_ENTRY_LIMIT",
    "INVENTORY_UNSAFE_NAME", "INVENTORY_PATH_LIMIT", "INVENTORY_UNSAFE_ENTRY", "INVENTORY_SOURCE_CHANGED",
    "INVENTORY_FILE_LIMIT", "INVENTORY_TOTAL_LIMIT", "INVENTORY_READ_FAILED", "FILE_CHECK_INCOMPLETE", "VALIDATION_BUSY"})


def safe_error_code(error):
    if isinstance(error, InventoryError) and str(error) in SAFE_INVENTORY_ERRORS: return str(error)
    for kind, code in ((MaterialFolderNotFound, "MATERIAL_FOLDER_NOT_FOUND"),
        (MaterialsRootUnavailable, "MATERIALS_ROOT_UNAVAILABLE"),
        (SecureFilesystemAccessUnavailable, "SECURE_FILESYSTEM_ACCESS_UNAVAILABLE"),
        (UnsafeMaterialPath, "UNSAFE_MATERIAL_PATH")):
        if isinstance(error, kind): return code
    if isinstance(error, ValueError) and str(error) in {"FILE_CHECK_REQUEST_INVALID", "FILE_CHECK_PATH_INVALID", "FILE_CHECK_STAGING_UNAVAILABLE"}: return str(error)
    return "LOCAL_FILE_CHECK_FAILED"


class FileCheckRunError(RuntimeError):
    def __init__(self, error, identifier, index, total):
        self.code = safe_error_code(error)
        self.material_id = identifier
        self.material_index = index
        self.total = total
        super().__init__(self.code)


def _process_material(root, parts, identifier, index, options, events):
    def update(value): events.put(("progress", index, value))
    try:
        value = check_material_files(root, parts, execution=MaterialExecution(options, update))
        events.put(("result", index, {"id": identifier,
            **{key: value[key] for key in ("profile", "complete", "issues", "report")}}))
    except Exception as error:
        events.put(("error", index, safe_error_code(error)))


def _parallel(root, parsed, options, workers, progress):
    # Spawn (not fork) isolates the semaphore, decoder children and memory of
    # each material. Only this coordinator publishes progress or final results.
    context = multiprocessing.get_context("spawn")
    events = context.Queue(maxsize=128)
    running = {}; active = {}; counters = {}; results = {}; failure = None; next_index = 1
    previous = None; last_publish = 0.0
    def publish(*, force=False):
        nonlocal previous, last_publish
        if progress is not None:
            value = {"schema_version": 2, "total": len(parsed), "completed": len(results),
                "active": [dict(active[index]) for index in sorted(active)],
                "cache_hits": sum(item[0] for item in counters.values()),
                "cache_misses": sum(item[1] for item in counters.values())}
            now = time.monotonic()
            if value == previous or (not force and now - last_publish < .25): return
            progress(value)
            previous = value; last_publish = now
    try:
        while running or (next_index <= len(parsed) and failure is None):
            while failure is None and next_index <= len(parsed) and len(running) < workers:
                index = next_index; next_index += 1
                identifier, parts = parsed[index - 1]
                active[index] = {"material_id": identifier, "material_index": index, "file": "", "phase": "QUEUED"}
                counters[index] = (0, 0)
                process = context.Process(target=_process_material, args=(root, parts, identifier, index, options, events))
                process.start(); running[index] = process
            publish()
            try:
                kind, index, value = events.get(timeout=.2)
            except Empty:
                exited = [(index, process) for index, process in running.items() if process.exitcode is not None]
                try:
                    # Recheck the queue after observing exit: a terminal event
                    # can arrive between the timed get and that observation.
                    kind, index, value = events.get_nowait()
                except Empty:
                    for index, process in exited:
                        failure = failure or (index, "LOCAL_FILE_CHECK_FAILED")
                        process.join(); running.pop(index); active.pop(index)
                    continue
            if index not in running: continue
            if kind == "progress":
                active[index].update({key: value[key] for key in ("phase", "file")})
                counters[index] = (value["cache_hits"], value["cache_misses"])
            else:
                process = running.pop(index)
                process.join(); active.pop(index)
                if kind == "result": results[index] = value
                else: failure = failure or (index, value)
        publish(force=True)
        if failure is not None:
            index, code = failure
            error = FileCheckRunError(RuntimeError(), parsed[index - 1][0], index, len(parsed))
            error.code = code
            raise error
        return {"schema_version": 1, "results": [results[index] for index in range(1, len(parsed) + 1)]}
    finally:
        # On an ordinary item failure, drain the other bounded material check
        # above before returning failure. This lets its snapshot context clean
        # up and never leaves an orphan decoder. Container cancellation removes
        # all children and its anonymous staging volume together.
        while any(process.is_alive() for process in running.values()):
            try: events.get(timeout=.2)
            except Empty: pass
        for process in running.values(): process.join()
        events.close(); events.join_thread()


def run(root: Path, request: dict, *, progress=None, execution_options=None, workers=2) -> dict:
    if not isinstance(request, dict) or set(request) != {"schema_version", "materials"} or type(request["schema_version"]) is not int or request["schema_version"] != 1:
        raise ValueError("FILE_CHECK_REQUEST_INVALID")
    materials = request["materials"]
    if not isinstance(materials, list) or not 1 <= len(materials) <= 100:
        raise ValueError("FILE_CHECK_REQUEST_INVALID")
    seen = set(); parsed = []
    for item in materials:
        if not isinstance(item, dict) or set(item) != {"id", "folder_path"} or not isinstance(item["id"], str) or not isinstance(item["folder_path"], str):
            raise ValueError("FILE_CHECK_REQUEST_INVALID")
        identifier = str(UUID(item["id"]))
        if identifier != item["id"] or identifier in seen: raise ValueError("FILE_CHECK_REQUEST_INVALID")
        seen.add(identifier)
        parts = tuple(item["folder_path"].split("/"))
        if not parts or len(item["folder_path"].encode("utf-8")) > 2048 or not all(_safe_name(part) for part in parts):
            raise ValueError("FILE_CHECK_REQUEST_INVALID")
        parsed.append((identifier, parts))
    if type(workers) is not int or workers not in {1, 2}: raise ValueError("FILE_CHECK_REQUEST_INVALID")
    if execution_options is not None:
        return _parallel(root, parsed, execution_options, workers, progress)
    results = []
    for index, (identifier, parts) in enumerate(parsed, start=1):
        if progress is not None:
            progress({"schema_version": 1, "material_id": identifier, "material_index": index, "total": len(parsed)})
        try:
            value = check_material_files(root, parts)
        except Exception as error:
            raise FileCheckRunError(error, identifier, index, len(parsed)) from None
        results.append({"id": identifier, **{key: value[key] for key in ("profile", "complete", "issues", "report")}})
    return {"schema_version": 1, "results": results}


def main():
    def write_progress(value):
        # Coordinator-owned diagnostics contain request UUIDs and relative
        # input names only. Never source contents or exception strings.
        temporary = Path("/output/progress.tmp")
        with temporary.open("w", encoding="utf-8") as target:
            json.dump(value, target, ensure_ascii=True)
            target.flush(); os.fsync(target.fileno())
        try: temporary.replace("/output/progress.json")
        except PermissionError: pass  # A Windows reader may momentarily pin it.
    try:
        source = Path("/request/request.json")
        if source.stat().st_size > 1024 * 1024: raise ValueError("FILE_CHECK_REQUEST_INVALID")
        namespace = os.environ.get("REAWOTE_CHECK_CACHE_NAMESPACE", "")
        options = ExecutionOptions(Path("/staging"), Path("/cache"), Path("/cache-key/private.key"), namespace)
        workers = int(os.environ.get("REAWOTE_CHECK_WORKERS", "2"))
        result = run(Path("/materials"), json.loads(source.read_bytes()), progress=write_progress,
            execution_options=options, workers=workers)
    except FileCheckRunError as error:
        result = {"schema_version": 1, "results": [], "error_code": error.code,
            "material_id": error.material_id, "material_index": error.material_index, "total": error.total}
    except Exception as error:
        # Infrastructure and source races are not completed material checks.
        result = {"schema_version": 1, "results": [], "error_code": safe_error_code(error)}
    with Path("/output/result.json").open("x", encoding="utf-8") as target:
        json.dump(result, target, ensure_ascii=True, indent=2)
        target.flush(); os.fsync(target.fileno())


if __name__ == "__main__": main()
