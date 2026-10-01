"""Explicit order-root filesystem operations, with durable crash recovery.

Only immediate children of the configured root are eligible. Bootstrap/import
does not call the enqueue hook; existing NAS folders are never adopted by name.
"""
from datetime import UTC, datetime
import os
from pathlib import Path
import re
import stat
from uuid import uuid4

from sqlalchemy import select

from app.db.notion_sync_models import OrderFolderOperation
from app.path_settings import current_paths, lock_paths


class OrderFolderError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def validate_folder_name(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 255
            or not re.fullmatch(r"[0-9]{4}_.+", value) or value.endswith((".", " "))
            or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in value)):
        raise OrderFolderError("ORDER_FOLDER_NAME_INVALID")
    return value


def _normal_directory(path):
    try: info = path.lstat()
    except OSError: raise OrderFolderError("ORDER_FOLDER_UNAVAILABLE") from None
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)):
        raise OrderFolderError("ORDER_FOLDER_LINK_FORBIDDEN")
    return info


def checked_root(value):
    root = Path(value)
    if not value or not root.is_absolute(): raise OrderFolderError("ORDER_FOLDER_ROOT_REQUIRED")
    # Resolve no user-supplied path through symlinks or junctions, including parents.
    for part in reversed((root, *root.parents)):
        _normal_directory(part)
    return root


def _identity(path):
    info = _normal_directory(path)
    return f"{info.st_dev}:{info.st_ino}"


def child_path(root, path):
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.parent != root or candidate.name in {".", ".."}:
        raise OrderFolderError("ORDER_FOLDER_OUTSIDE_ROOT")
    validate_folder_name(candidate.name)
    return candidate


def known_folder_numbers(root_path):
    root = checked_root(root_path)
    try:
        return {child.name[:4] for child in root.iterdir() if re.match(r"^[0-9]{4}_", child.name)}
    except OSError: raise OrderFolderError("ORDER_FOLDER_UNAVAILABLE") from None


def folder_preflight(root_path, target_name, source_path=None):
    root = checked_root(root_path)
    target = root / validate_folder_name(target_name)
    source = child_path(root, source_path) if source_path else None
    source_identity = _identity(source) if source else None
    if source == target: raise OrderFolderError("ORDER_FOLDER_ALREADY_CURRENT")
    if source and source.name[:4] != target.name[:4]: raise OrderFolderError("ORDER_NUMBER_MISMATCH")
    if os.path.lexists(target): raise OrderFolderError("ORDER_FOLDER_COLLISION")
    for child in root.iterdir():
        if child.name.startswith(target_name[:4] + "_") and child != source:
            raise OrderFolderError("ORDER_NUMBER_FOLDER_CONFLICT")
    return str(target), source_identity


def _rename_no_replace(source, target):
    if os.name == "nt":
        # Windows rename fails when the destination exists, including a directory.
        os.rename(source, target)
    elif os.name == "posix":
        import ctypes
        import errno
        library = ctypes.CDLL(None, use_errno=True)
        rename = getattr(library, "renameat2", None)
        if rename is None: raise OrderFolderError("ORDER_RENAME_UNSUPPORTED")
        if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
            code = ctypes.get_errno()
            if code == errno.EEXIST: raise OrderFolderError("ORDER_FOLDER_COLLISION")
            raise OSError(code, os.strerror(code))
    else: raise OrderFolderError("ORDER_RENAME_UNSUPPORTED")


def apply_folder_operation(root_path, operation):
    target_path, expected_identity = folder_preflight(root_path, operation.target_name, operation.source_path)
    target = Path(target_path)
    if operation.action == "CREATE":
        target.mkdir()  # No parents=True or exist_ok: never replace/adopt another folder.
    elif operation.action == "RENAME":
        if expected_identity != operation.source_identity: raise OrderFolderError("ORDER_FOLDER_CHANGED")
        _rename_no_replace(Path(operation.source_path), target)
    else: raise OrderFolderError("ORDER_FOLDER_ACTION_INVALID")
    return str(target)


def enqueue_order_folder_create(session, order, actor_id):
    if order.folder_path: return None
    session.flush()
    from app.material_review import canonical_hash
    operation = OrderFolderOperation(order_id=order.id, actor_id=actor_id, request_key=uuid4(),
        request_hash=canonical_hash({"order_id": str(order.id), "action": "CREATE", "target_name": order.name}),
        action="CREATE", target_name=order.name, status="PENDING")
    session.add(operation)
    return operation


def folder_status(session, order_id):
    operation = session.scalar(select(OrderFolderOperation).where(OrderFolderOperation.order_id == order_id)
        .order_by(OrderFolderOperation.created_at.desc(), OrderFolderOperation.id.desc()).limit(1))
    if operation is None: return {"status": "NOT_QUEUED", "error_code": None}
    return {"status": operation.status, "error_code": operation.error_code,
        "operation_id": str(operation.id), "action": operation.action, "target_name": operation.target_name}


def process_next_folder_operation(database, settings):
    if not settings.order_folders_enabled: return False
    from app.db.models import InternalUser, Project
    with database.session() as session:
        lock_paths(session)
        operation = session.scalar(select(OrderFolderOperation).where(OrderFolderOperation.status == "PENDING")
            .order_by(OrderFolderOperation.created_at).with_for_update(skip_locked=True).limit(1))
        if operation is None: return False
        actor = session.get(InternalUser, operation.actor_id)
        if actor is None or not actor.is_active or actor.role not in {"ADMIN", "PRODUCTION_LEAD"}:
            operation.status, operation.error_code = "ERROR", "ORDER_FOLDER_ACTOR_NOT_ALLOWED"
            session.commit(); return True
        order = session.scalar(select(Project).where(Project.id == operation.order_id).with_for_update())
        if order is None or order.name != operation.target_name or order.folder_path != operation.source_path:
            operation.status, operation.error_code = "ERROR", "ORDER_FOLDER_RECORD_CHANGED"
            session.commit(); return True
        try:
            root_path = current_paths(session, settings)["orders_root"]
            result_path, identity = folder_preflight(root_path, operation.target_name, operation.source_path)
        except (OrderFolderError, OSError) as error:
            operation.status, operation.error_code = "ERROR", getattr(error, "code", "ORDER_FOLDER_UNAVAILABLE")
            session.commit(); return True
        if operation.action == "RENAME" and operation.source_identity != identity:
            operation.status, operation.error_code = "ERROR", "ORDER_FOLDER_CHANGED"
            session.commit(); return True
        operation.status = "RUNNING"
        operation.result_path = result_path
        identifier = operation.id
        session.commit()
    failure = None
    try: result_path = apply_folder_operation(root_path, operation)
    except (OrderFolderError, OSError) as error: failure = getattr(error, "code", "ORDER_FOLDER_UNAVAILABLE")
    with database.session() as session:
        operation = session.scalar(select(OrderFolderOperation).where(OrderFolderOperation.id == identifier).with_for_update())
        order = session.scalar(select(Project).where(Project.id == operation.order_id).with_for_update())
        if failure:
            operation.status, operation.error_code = "ERROR", failure
        elif order.name != operation.target_name or order.folder_path != operation.source_path:
            operation.status, operation.error_code = "RECONCILE", "ORDER_FOLDER_RECORD_CHANGED_AFTER_OPERATION"
        else:
            from app.api.directory import _append_change
            from app.customer_orders import directory_snapshot
            before = directory_snapshot(order)
            order.folder_path = result_path
            _append_change(session, order, operation.actor_id, before,
                "FOLDER_CREATED" if operation.action == "CREATE" else "FOLDER_RENAMED")
            operation.status, operation.error_code, operation.completed_at = "COMPLETED", None, datetime.now(UTC)
        session.commit()
    return True


def recover_interrupted_folders(database):
    with database.session() as session:
        for operation in session.scalars(select(OrderFolderOperation).where(OrderFolderOperation.status == "RUNNING").with_for_update()):
            # Do not adopt a folder simply because a path exists after a crash.
            operation.status, operation.error_code = "RECONCILE", "ORDER_FOLDER_INTERRUPTED"
        session.commit()
