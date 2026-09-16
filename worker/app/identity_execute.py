"""Recoverable confirmed identity changes; source mutations are opt-in only.

The caller must own an exclusive durable material operation before invoking this
worker. Ordinary inspection and packaging never call this module.
"""
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import stat

from app.file_journal import JournalError, open_journal, rename_noreplace
from app.identity_plan import IdentityTarget, plan_identity_change, renamed_component, rewrite_metadata
from app.inventory import inventory_material
from app.secure_filesystem import MaterialFolderNotFound, _metadata_bytes, _metadata_flags, open_material_directory


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def _identity(info): return [info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)]


def _file_signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def _entry(root, path):
    parts = tuple(path.split("/"))
    try:
        with open_material_directory(root, parts[:-1]) as fd:
            return os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
    except (FileNotFoundError, MaterialFolderNotFound): return None


def _matches(info, identity): return info is not None and _identity(info) == identity


def _rename(root, source, target):
    old = tuple(source.split("/")); new = tuple(target.split("/"))
    with open_material_directory(root, old[:-1]) as old_fd, open_material_directory(root, new[:-1]) as new_fd:
        rename_noreplace(old_fd, old[-1], new_fd, new[-1])


def _rename_step(root, step, *, reverse=False):
    source = step["target"] if reverse else step["source"]
    target = step["source"] if reverse else step["target"]
    source_info = _entry(root, source); target_info = _entry(root, target)
    if _matches(target_info, step["identity"]) and source_info is None: return  # Already applied.
    if not _matches(source_info, step["identity"]): raise JournalError("IDENTITY_ENTRY_CHANGED")
    if target_info is not None: raise JournalError("JOURNAL_TARGET_EXISTS")
    _rename(root, source, target)
    if not _matches(_entry(root, target), step["identity"]): raise JournalError("IDENTITY_ENTRY_CHANGED")


def _read_metadata_at(root, path):
    with open_material_directory(root, tuple(path.split("/"))) as fd:
        raw, error = _metadata_bytes(fd)
        if error: raise JournalError("IDENTITY_METADATA_CHANGED")
        info = os.stat("metadata.txt", dir_fd=fd, follow_symlinks=False)
        return raw, info


def _replace_metadata(root, step, raw, expected, *, reverse=False, journal=None, state=None):
    current, info = _read_metadata_at(root, step["directory"])
    desired = hashlib.sha256(raw).hexdigest()
    current_hash = hashlib.sha256(current).hexdigest()
    temporary = step["temporary"]
    with open_material_directory(root, tuple(step["directory"].split("/"))) as fd:
        # An interrupted atomic replacement may leave the owned temporary file.
        try:
            temporary_fd = os.open(temporary, _metadata_flags(), dir_fd=fd)
        except FileNotFoundError: temporary_fd = None
        if temporary_fd is not None:
            try:
                temporary_info = os.fstat(temporary_fd)
                if (not reverse or not _matches(temporary_info, step.get("temporary_identity"))
                        or not stat.S_ISREG(temporary_info.st_mode) or temporary_info.st_nlink != 1):
                    raise JournalError("IDENTITY_ENTRY_CHANGED")
            finally: os.close(temporary_fd)
            os.unlink(temporary, dir_fd=fd); os.fsync(fd)
        if current_hash == desired: return
        if current_hash != expected or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise JournalError("IDENTITY_METADATA_CHANGED")
        temporary_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
        try:
            own = os.fstat(temporary_fd)
            step["temporary_identity"] = _identity(own)
            if journal is not None: journal.write(state)
            if (own.st_uid, own.st_gid) != (step["uid"], step["gid"]): os.fchown(temporary_fd, step["uid"], step["gid"])
            os.fchmod(temporary_fd, step["mode"])
            remaining = memoryview(raw)
            while remaining:
                count = os.write(temporary_fd, remaining)
                if count <= 0: raise JournalError("JOURNAL_WRITE_FAILED")
                remaining = remaining[count:]
            if reverse: os.utime(temporary_fd, ns=(step["atime_ns"], step["mtime_ns"]))
            os.fsync(temporary_fd)
        finally: os.close(temporary_fd)
        # Refuse an external replacement between reading and the atomic write.
        if (_file_signature(os.stat("metadata.txt", dir_fd=fd, follow_symlinks=False)) != _file_signature(info)
                or not _matches(os.stat(temporary, dir_fd=fd, follow_symlinks=False), step["temporary_identity"])):
            raise JournalError("IDENTITY_METADATA_CHANGED")
        os.replace(temporary, "metadata.txt", src_dir_fd=fd, dst_dir_fd=fd); os.fsync(fd)


def _restore_directory_times(root, state, *, original):
    base = state["source_path"] if original else state["target_path"]
    for item in sorted(state["directories"], key=lambda item: item["source"].count("/"), reverse=True):
        relative = item["source"] if original else item["target"]
        path = base + ("/" + relative if relative else "")
        with open_material_directory(root, tuple(path.split("/"))) as fd:
            info = os.fstat(fd)
            if not _matches(info, item["identity"]): raise JournalError("IDENTITY_ENTRY_CHANGED")
            os.utime(fd, ns=(info.st_atime_ns, item["mtime_ns"])); os.fsync(fd)


def _prepare(root, source_parts, target, operation_id, expected_hash, journal, request_hash):
    plan = plan_identity_change(root, source_parts, target)
    if not plan["ready"]: raise JournalError("IDENTITY_PLAN_BLOCKED")
    if plan["plan_hash"] != expected_hash: raise JournalError("IDENTITY_PLAN_CHANGED")
    inventory = inventory_material(root, source_parts)
    if inventory["source_revision_hash"] != plan["source_revision_hash"]: raise JournalError("IDENTITY_PLAN_CHANGED")
    source_path = "/".join(source_parts); destination = target.parts()
    temp_root = "/".join((*source_parts[:-1], ".reawote-identity-" + operation_id))
    if _entry(root, temp_root) is not None: raise JournalError("JOURNAL_TARGET_EXISTS")
    root_info = _entry(root, source_path)
    groups = defaultdict(list); directories = [{"source": "", "target": "", "identity": _identity(root_info), "mtime_ns": root_info.st_mtime_ns}]
    expected_inventory = json.loads(json.dumps(inventory))
    expected_inventory["folder_name"] = destination[-1]
    for index, entry in enumerate(inventory["entries"]):
        parts = entry["path"].split("/")
        mapped = "/".join(renamed_component(part, source_parts[-1], destination[-1]) for part in parts)
        info = _entry(root, source_path + "/" + entry["path"])
        if info is None: raise JournalError("IDENTITY_PLAN_CHANGED")
        if entry["kind"] == "directory": directories.append({"source": entry["path"], "target": mapped, "identity": _identity(info), "mtime_ns": info.st_mtime_ns})
        expected_inventory["entries"][index]["path"] = mapped
        new_name = renamed_component(parts[-1], source_parts[-1], destination[-1])
        if parts[-1] != new_name:
            parent = "/".join(parts[:-1]); temp = f".reawote-item-{operation_id}-{index}"
            if _entry(root, source_path + "/" + (parent + "/" if parent else "") + temp) is not None: raise JournalError("JOURNAL_TARGET_EXISTS")
            groups[parent].append((parts[-1], temp, new_name, _identity(info)))
    steps = [{"kind": "rename", "source": source_path, "target": temp_root, "identity": _identity(root_info)}]
    for parent in sorted(groups, key=lambda value: (value.count("/") + bool(value), value), reverse=True):
        base = temp_root + ("/" + parent if parent else "") + "/"
        for source, temporary, _, identity in groups[parent]: steps.append({"kind": "rename", "source": base + source, "target": base + temporary, "identity": identity})
        for _, temporary, name, identity in groups[parent]: steps.append({"kind": "rename", "source": base + temporary, "target": base + name, "identity": identity})
    state = {"schema_version": 1, "request_hash": request_hash, "plan_hash": expected_hash, "operation_id": operation_id,
        "status": "PREPARING", "source_path": source_path, "target_path": target.path, "temporary_path": temp_root,
        "root_identity": _identity(root_info), "source_revision_hash": inventory["source_revision_hash"],
        "directories": directories, "steps": steps, "next_step": 0,
        "target": {"path": target.path, "brand_name": target.brand_name, "material_name": target.material_name}}
    journal.write(state)  # No source mutation may precede this durable checkpoint.
    if plan["metadata"]["changed_fields"]:
        if _entry(root, source_path + "/.reawote-metadata-" + operation_id) is not None:
            raise JournalError("JOURNAL_TARGET_EXISTS")
        raw, info = _read_metadata_at(root, source_path)
        rewritten, _ = rewrite_metadata(raw, source_parts[-1], target)
        if hashlib.sha256(raw).hexdigest() != plan["metadata"]["before_hash"] or hashlib.sha256(rewritten).hexdigest() != plan["metadata"]["after_hash"]:
            raise JournalError("IDENTITY_PLAN_CHANGED")
        journal.backup_metadata(raw)
        steps.append({"kind": "metadata", "directory": temp_root, "temporary": ".reawote-metadata-" + operation_id,
            "before_hash": plan["metadata"]["before_hash"], "after_hash": plan["metadata"]["after_hash"],
            "uid": info.st_uid, "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode), "mtime_ns": info.st_mtime_ns, "atime_ns": info.st_atime_ns})
        metadata_entry = next(item for item in expected_inventory["entries"] if item["path"] == "metadata.txt")
        metadata_entry.update(size=len(rewritten), sha256=plan["metadata"]["after_hash"])
    steps.append({"kind": "rename", "source": temp_root, "target": target.path, "identity": _identity(root_info)})
    expected_inventory["entries"].sort(key=lambda item: item["path"])
    state["target_revision_hash"] = _hash({key: expected_inventory[key] for key in ("schema_version", "folder_name", "master_resolution", "policy", "entries")})
    state.update(status="PREPARED", steps=steps)
    journal.write(state)
    return state


def _result(state, status, failure=None):
    return {"operation_id": state["operation_id"], "status": status, "plan_hash": state["plan_hash"],
        "source_path": state["source_path"], "target_path": state["target_path"],
        "source_revision_hash": None if status == "REJECTED" else state["source_revision_hash"],
        "target_revision_hash": state.get("target_revision_hash") if status == "COMPLETED" else None, "failure_code": failure}


def _after_step(index):
    """Internal fault-injection seam; never exposed through the worker API."""


def _rollback(root, journal, state):
    try:
        # Covers a crash after full rollback but before its final checkpoint.
        restored = _matches(_entry(root, state["source_path"]), state["root_identity"])
        if not restored:
            for step in reversed(state["steps"][:state["next_step"] + 1]):
                if step["kind"] == "rename":
                    if _matches(_entry(root, step["source"]), step["identity"]): continue
                    _rename_step(root, step, reverse=True)
                else:
                    original = journal.original_metadata()
                    if hashlib.sha256(original).hexdigest() != step["before_hash"]: raise JournalError("JOURNAL_INVALID_STATE")
                    _replace_metadata(root, step, original, step["after_hash"], reverse=True, journal=journal, state=state)
        _restore_directory_times(root, state, original=True)
        inventory = inventory_material(root, tuple(state["source_path"].split("/")))
        if inventory["source_revision_hash"] != state["source_revision_hash"]: raise JournalError("IDENTITY_ENTRY_CHANGED")
        result = _result(state, "ROLLED_BACK", "IDENTITY_OPERATION_FAILED")
    except Exception:
        result = _result(state, "RECOVERY_REQUIRED", "IDENTITY_RECOVERY_REQUIRED")
    state.update(status=result["status"], result=result)
    journal.write(state)
    return result


def execute_identity_change(root: Path, journal_root: Path, operation_id: str, source_parts: tuple[str, ...],
                            target: IdentityTarget, expected_plan_hash: str, *, enabled=False):
    if enabled is not True: raise JournalError("SOURCE_MUTATIONS_DISABLED")
    if journal_root.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(journal_root.resolve()):
        raise JournalError("JOURNAL_ROOT_OVERLAPS_SOURCE")
    request_hash = _hash({"source": list(source_parts), "target": target.__dict__, "plan_hash": expected_plan_hash})
    with open_journal(journal_root, operation_id) as journal:
        state = journal.read()
        if state is not None:
            if state.get("schema_version") != 1 or state.get("request_hash") != request_hash: raise JournalError("JOURNAL_REQUEST_CONFLICT")
            if state["status"] == "REJECTED": return state["result"]
            if state["status"] in {"COMPLETED", "ROLLED_BACK"}:
                path = state["target_path"] if state["status"] == "COMPLETED" else state["source_path"]
                if not _matches(_entry(root, path), state["root_identity"]): raise JournalError("IDENTITY_ENTRY_CHANGED")
                return state["result"]
            # No source action starts in PREPARING; an interrupted prepare is safe
            # to fail without replaying partially written private backups.
            if state["status"] == "PREPARING":
                state.update(status="REJECTED", result=_result(state, "REJECTED", "IDENTITY_PREPARATION_INTERRUPTED"))
                journal.write(state); return state["result"]
            return _rollback(root, journal, state)
        try:
            state = _prepare(root, source_parts, target, operation_id, expected_plan_hash, journal, request_hash)
        except Exception:
            # Preparation can write private checkpoints/backups but never source
            # files. Persist this terminal outcome so a changed/rejected plan does
            # not leave the coordinator locked forever after a lost response.
            result = {"operation_id": operation_id, "status": "REJECTED", "plan_hash": expected_plan_hash,
                "source_path": "/".join(source_parts), "target_path": target.path, "source_revision_hash": None,
                "target_revision_hash": None, "failure_code": "IDENTITY_PREPARATION_REJECTED"}
            journal.write({"schema_version": 1, "request_hash": request_hash, "status": "REJECTED", "result": result})
            return result
        try:
            for index, step in enumerate(state["steps"]):
                state.update(status="EXECUTING", next_step=index); journal.write(state)
                if step["kind"] == "rename": _rename_step(root, step)
                else:
                    raw = journal.original_metadata()
                    if hashlib.sha256(raw).hexdigest() != step["before_hash"]: raise JournalError("JOURNAL_INVALID_STATE")
                    rewritten, _ = rewrite_metadata(raw, source_parts[-1], target)
                    _replace_metadata(root, step, rewritten, step["before_hash"], journal=journal, state=state)
                _after_step(index)
                state["next_step"] = index + 1; journal.write(state)
            _restore_directory_times(root, state, original=False)
            inventory = inventory_material(root, target.parts())
            if inventory["source_revision_hash"] != state["target_revision_hash"]: raise JournalError("IDENTITY_OUTPUT_CHANGED")
            result = _result(state, "COMPLETED")
            state.update(status="COMPLETED", result=result); journal.write(state)
            return result
        except Exception:
            return _rollback(root, journal, state)
