"""Open only a recorded order folder on the explicitly enabled local desktop."""
from contextlib import contextmanager, ExitStack
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.auth.access import AccessDependency
from app.db.models import Project
from app.db.notion_sync_models import OrderFolderOperation
from app.local_filesystem import FileInfo, LocalFilesError
from app.order_folders import child_path, checked_root, OrderFolderError
from app.path_settings import current_paths, lock_paths


class _OrderDirectoryHandles:
    """Read-only handles for a local, UNC, or mapped-drive order directory."""
    def __init__(self):
        if os.name != 'nt': raise LocalFilesError('LOCAL_FILES_WINDOWS_REQUIRED')
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self.kernel.CreateFileW.restype = wintypes.HANDLE
        self.kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInfo)]
        self.kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    @contextmanager
    def opened(self, path):
        # Read attributes only; refuse reparse points and hold against deletion
        # or renaming until Explorer has been launched.
        handle = self.kernel.CreateFileW(str(path), 0x80, 3, None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise LocalFilesError('LOCAL_FILE_UNAVAILABLE')
        try:
            info = FileInfo()
            if not self.kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
                raise LocalFilesError('LOCAL_FILE_UNVERIFIED')
            if not info.attrs & 0x10 or info.attrs & 0x400:
                raise LocalFilesError('LOCAL_PATH_UNSAFE')
            final = ctypes.create_unicode_buffer(32768)
            length = self.kernel.GetFinalPathNameByHandleW(handle, final, len(final), 0)
            if not 0 < length < len(final): raise LocalFilesError('LOCAL_PATH_UNSAFE')
            value = final.value
            if value.startswith('\\\\?\\UNC\\'): value = '\\\\' + value[8:]
            elif value.startswith('\\\\?\\'): value = value[4:]
            else: raise LocalFilesError('LOCAL_PATH_UNSAFE')
            verified = Path(value)
            if not verified.is_absolute() or '..' in verified.parts:
                raise LocalFilesError('LOCAL_PATH_UNSAFE')
            yield verified
        finally:
            self.kernel.CloseHandle(handle)


@contextmanager
def _verified_order_directory(root, folder):
    # The recorded path must be an immediate child of the configured root.
    candidate = child_path(checked_root(root), folder)
    handles = _OrderDirectoryHandles()
    with ExitStack() as stack:
        current = Path(candidate.anchor)
        expected = stack.enter_context(handles.opened(current))
        for part in candidate.parts[1:]:
            current /= part
            expected /= part
            verified = stack.enter_context(handles.opened(current))
            # Mapped drives resolve to UNC paths. Compare each descendant to
            # the canonical anchor, catching redirected/replaced ancestors.
            if verified != expected: raise LocalFilesError('LOCAL_PATH_UNSAFE')
        yield expected


def open_order_folder(root, folder):
    with _verified_order_directory(root, folder) as verified:
        subprocess.Popen([str(Path(os.environ['WINDIR']) / 'explorer.exe'), str(verified)], close_fds=True)


def build_order_desktop_router(database, settings, *, opener=None):
    router = APIRouter(prefix='/api/orders', tags=['local order folders'])

    @router.post('/{order_id}/open-folder')
    def open_folder(order_id: UUID, access: AccessDependency, request: Request):
        with database.session() as session:
            access.check(session)
            if request.client is None or request.client.host not in {'127.0.0.1', '::1', 'testclient'}:
                raise HTTPException(403, {'code': 'LOCAL_DESKTOP_ONLY'})
            if opener is None:
                raise HTTPException(503, {'code': 'LOCAL_DESKTOP_UNAVAILABLE'})
            lock_paths(session)
            order = session.scalar(select(Project).where(Project.id == order_id).with_for_update())
            if order is None: raise HTTPException(404, 'Order not found.')
            if not order.folder_path: raise HTTPException(409, {'code': 'ORDER_FOLDER_REQUIRED'})
            if session.scalar(select(OrderFolderOperation.id).where(OrderFolderOperation.order_id == order_id,
                    OrderFolderOperation.status.in_(['PENDING', 'RUNNING', 'RECONCILE'])).limit(1)):
                raise HTTPException(409, {'code': 'ORDER_FOLDER_OPERATION_PENDING'})
            # Retain the actor and order locks until the desktop action is launched.
            access.check(session)
            try: opener(current_paths(session, settings)['orders_root'], order.folder_path)
            except (OSError, ValueError, OrderFolderError, LocalFilesError):
                raise HTTPException(409, {'code': 'ORDER_FOLDER_UNAVAILABLE'}) from None
            return {'opened': True}

    return router
