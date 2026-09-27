"""Bounded, explicit edits of root production metadata; identity keys stay opaque."""
from decimal import Decimal
import hashlib
import json
import os
import re
import stat

from app.file_journal import JournalError, open_journal, rename_noreplace
from app.preflight import MAX_METADATA_BYTES, _reject_constant, _unique_object
from app.secure_filesystem import _metadata_bytes, open_material_directory
from app.source_metadata import dimension_fits_storage, normalize_hex, parse_source_metadata_bytes


FIELDS = frozenset({"hex_color", "width_cm", "height_cm"})


def normalize_values(values):
    if not isinstance(values, dict) or set(values) != FIELDS:
        raise JournalError("METADATA_VALUES_INVALID")
    result = {}
    for field, value in values.items():
        if value is None:
            result[field] = None
        elif field == "hex_color":
            try: result[field] = normalize_hex(value)
            except ValueError: raise JournalError("METADATA_VALUES_INVALID") from None
        else:
            if not isinstance(value, str) or len(value) > 32 or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
                raise JournalError("METADATA_VALUES_INVALID")
            number = Decimal(value)
            if not dimension_fits_storage(number): raise JournalError("METADATA_VALUES_INVALID")
            result[field] = format(number, "f").rstrip("0").rstrip(".") if "." in value else str(number)
    return result


def _serialize(value, depth=0):
    if depth > 64: raise JournalError("METADATA_FORMAT_UNSUPPORTED")
    if isinstance(value, Decimal): return str(value)
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k, ensure_ascii=True) + ":" + _serialize(v, depth + 1) for k, v in value.items()) + "}"
    if isinstance(value, list): return "[" + ",".join(_serialize(v, depth + 1) for v in value) + "]"
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def rewrite_metadata(raw, values):
    values = normalize_values(values)
    if raw is None:
        data = {}
    else:
        if len(raw) > MAX_METADATA_BYTES: raise JournalError("SOURCE_METADATA_TOO_LARGE")
        parsed = parse_source_metadata_bytes(raw)
        if parsed.status == "INVALID": raise JournalError("METADATA_FORMAT_UNSUPPORTED")
        try:
            text = raw.decode("utf-8")
            if text.lstrip().startswith("{"):
                data = json.loads(text, parse_float=Decimal, parse_int=Decimal,
                                  parse_constant=_reject_constant, object_pairs_hook=_unique_object)
            else:
                # Preserve every legacy line when migrating the dimensions-only format.
                data = {"LEGACY_SOURCE_TEXT": text}
            if not isinstance(data, dict): raise ValueError()
            for key in ("COLOR", "TEXTURE_SIZE"):
                if key in data and not isinstance(data[key], dict): raise ValueError()
            if "cm" in data.get("TEXTURE_SIZE", {}) and not isinstance(data["TEXTURE_SIZE"]["cm"], dict): raise ValueError()
        except (ValueError, UnicodeError, RecursionError): raise JournalError("METADATA_FORMAT_UNSUPPORTED") from None
    color = data.setdefault("COLOR", {})
    cm = data.setdefault("TEXTURE_SIZE", {}).setdefault("cm", {})
    for parent, key, value in ((color, "hex", values["hex_color"]), (cm, "width", values["width_cm"]), (cm, "height", values["height_cm"])):
        if value is None: parent.pop(key, None)
        else: parent[key] = value if key == "hex" else Decimal(value)
    result = (_serialize(data) + "\n").encode("utf-8")
    if len(result) > MAX_METADATA_BYTES: raise JournalError("SOURCE_METADATA_TOO_LARGE")
    return result


def _signature(info):
    return None if info is None else [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink]


def _identity(fd):
    info = os.fstat(fd)
    return [info.st_dev, info.st_ino]


def _read(fd):
    try: before = os.stat("metadata.txt", dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError: before = None
    if before is not None and (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1):
        raise JournalError("SOURCE_METADATA_UNSAFE_FILE")
    raw, error = _metadata_bytes(fd)
    if error and error[0] != "MISSING": raise JournalError("SOURCE_METADATA_" + error[0])
    try: after = os.stat("metadata.txt", dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError: after = None
    if _signature(before) != _signature(after): raise JournalError("METADATA_SOURCE_CHANGED")
    return raw, before


def metadata_view(raw, folder_name):
    parsed = parse_source_metadata_bytes(raw)
    return {"schema_version": 1, "folder_name": folder_name, "sha256": parsed.sha256,
            "status": parsed.status, "hex_color": parsed.hex_color,
            "width_cm": str(parsed.width_cm) if parsed.width_cm is not None else None,
            "height_cm": str(parsed.height_cm) if parsed.height_cm is not None else None,
            "raw_content": parsed.raw_content}


def inspect_metadata(root, parts):
    with open_material_directory(root, parts) as fd:
        raw, _ = _read(fd)
        view = metadata_view(raw, parts[-1])
        try: rewrite_metadata(raw, {name: view[name] for name in FIELDS}); editable = True
        except JournalError: editable = False
        return {**view, "editable": editable}


def _after_replace():
    """Private fault-injection seam for crash recovery tests."""


def _reject(journal, request_hash, operation_id, code):
    result = {"operation_id": operation_id, "status": "REJECTED", "failure_code": code, "metadata": None}
    journal.write({"request_hash": request_hash, "status": "REJECTED", "result": result})
    return result


def execute_metadata_edit(root, journal_root, operation_id, parts, expected_sha256, values, *, enabled=False):
    if enabled is not True: raise JournalError("SOURCE_MUTATIONS_DISABLED")
    if (not parts or any(not p or p in {".", ".."} or any(c in p for c in "/\\:\x00") for p in parts)
            or expected_sha256 is not None and re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None):
        raise JournalError("METADATA_VALUES_INVALID")
    values = normalize_values(values)
    if journal_root.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(journal_root.resolve()):
        raise JournalError("JOURNAL_ROOT_OVERLAPS_SOURCE")
    request_hash = hashlib.sha256(json.dumps({"kind": "metadata-edit-1", "parts": parts,
        "expected_sha256": expected_sha256, "values": values}, sort_keys=True).encode()).hexdigest()
    with open_journal(journal_root, operation_id) as journal, open_material_directory(root, parts) as fd:
        import fcntl
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise JournalError("JOURNAL_BUSY") from None
        state = journal.read()
        if state is not None:
            if state.get("request_hash") != request_hash: raise JournalError("JOURNAL_REQUEST_CONFLICT")
            if state["status"] in {"COMPLETED", "REJECTED"}: return state["result"]
            if state["identity"] != _identity(fd): raise JournalError("METADATA_SOURCE_CHANGED")
        try: raw, info = _read(fd)
        except JournalError as exc:
            if state is not None: raise
            return _reject(journal, request_hash, operation_id, str(exc))
        digest = hashlib.sha256(raw).hexdigest() if raw is not None else None
        if state is None:
            if digest != expected_sha256:
                return _reject(journal, request_hash, operation_id, "METADATA_SOURCE_CHANGED")
            try: desired = rewrite_metadata(raw, values)
            except JournalError as exc: return _reject(journal, request_hash, operation_id, str(exc))
            state = {"request_hash": request_hash, "status": "PREPARED", "identity": _identity(fd),
                     "desired": desired.decode("utf-8"), "sha256": hashlib.sha256(desired).hexdigest(),
                     "temporary": ".reawote-metadata-" + operation_id, "temporary_identity": None}
            journal.write(state)  # Durable authorization precedes every source mutation.
        desired = state["desired"].encode("utf-8")
        if digest != state["sha256"]:
            if digest != expected_sha256: raise JournalError("METADATA_SOURCE_CHANGED")
            temporary = state["temporary"]
            try: temporary_info = os.stat(temporary, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError: temporary_info = None
            if temporary_info is not None:
                if ([temporary_info.st_dev, temporary_info.st_ino] != state["temporary_identity"]
                        or not stat.S_ISREG(temporary_info.st_mode) or temporary_info.st_nlink != 1):
                    raise JournalError("METADATA_SOURCE_CHANGED")
                os.unlink(temporary, dir_fd=fd); os.fsync(fd)
            temporary_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
            try:
                state["temporary_identity"] = _identity(temporary_fd); journal.write(state)
                if info is not None:
                    own = os.fstat(temporary_fd)
                    if (own.st_uid, own.st_gid) != (info.st_uid, info.st_gid): os.fchown(temporary_fd, info.st_uid, info.st_gid)
                    os.fchmod(temporary_fd, stat.S_IMODE(info.st_mode))
                else:
                    # Generated color/dimensions must remain readable by a
                    # separate packaging service account after atomic creation.
                    os.fchmod(temporary_fd, 0o644)
                remaining = memoryview(desired)
                while remaining:
                    count = os.write(temporary_fd, remaining)
                    if count <= 0: raise JournalError("JOURNAL_WRITE_FAILED")
                    remaining = remaining[count:]
                os.fsync(temporary_fd)
            finally: os.close(temporary_fd)
            current, current_info = _read(fd)
            if _signature(info) != _signature(current_info) or current != raw: raise JournalError("METADATA_SOURCE_CHANGED")
            with open_material_directory(root, parts) as check_fd:
                if _identity(check_fd) != _identity(fd): raise JournalError("METADATA_SOURCE_CHANGED")
            temporary_info = os.stat(temporary, dir_fd=fd, follow_symlinks=False)
            if ([temporary_info.st_dev, temporary_info.st_ino] != state["temporary_identity"]
                    or not stat.S_ISREG(temporary_info.st_mode) or temporary_info.st_nlink != 1):
                raise JournalError("METADATA_SOURCE_CHANGED")
            if info is None: rename_noreplace(fd, temporary, fd, "metadata.txt")
            else: os.replace(temporary, "metadata.txt", src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd); _after_replace()
        verified, _ = _read(fd)
        if verified != desired: raise JournalError("METADATA_SOURCE_CHANGED")
        result = {"operation_id": operation_id, "status": "COMPLETED", "failure_code": None,
                  "metadata": metadata_view(desired, parts[-1])}
        state.update(status="COMPLETED", result=result); journal.write(state)
        return result
