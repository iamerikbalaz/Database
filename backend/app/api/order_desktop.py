"""Open only a recorded order folder on the explicitly enabled local desktop."""
import os
from pathlib import Path
import subprocess
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.auth.access import AccessDependency
from app.db.models import Project
from app.db.notion_sync_models import OrderFolderOperation
from app.local_filesystem import LocalFilesystem, LocalFilesError
from app.order_folders import child_path, checked_root, OrderFolderError
from app.path_settings import current_paths, lock_paths


def open_order_folder(root, folder):
    # Do not launch a user-supplied URL, command, root or arbitrary absolute path.
    candidate = child_path(checked_root(root), folder)
    filesystem = LocalFilesystem(root)
    with filesystem.directory(candidate.name) as verified:
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
