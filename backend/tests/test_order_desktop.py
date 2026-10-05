"""Opening a folder never accepts an arbitrary path or launches a live Explorer in tests."""
from fastapi.testclient import TestClient
import pytest

from app.db.models import Project
from app.local_filesystem import LocalFilesError
from app.main import create_app
from test_application_access import ORIGIN, PASSWORD, access_case
from test_local_files_api import LocalLibraryStub


@pytest.fixture
def order_desktop(access_case, monkeypatch):
    case = access_case
    calls = []
    monkeypatch.setattr('app.main.open_order_folder', lambda root, path: calls.append((root, path)))
    case.app = create_app(case.app.state.settings, case.database, case.worker, local_library=LocalLibraryStub())
    with case.database.session() as session:
        order = session.get(Project, case.materials[0].project_id)
        order.folder_path = r'R:\0. PROJECTS\0001_SYNTHETIC_SCANNING_102026'
        session.commit()
    return case, calls, order.id, order.folder_path


@pytest.mark.parametrize('role', [None, 'ADMIN', 'PRODUCTION_LEAD', 'PROCESSOR', 'LEADERSHIP'])
def test_open_order_requires_auth_and_uses_only_recorded_path(order_desktop, role):
    case, calls, identifier, folder = order_desktop
    with case.client(role) as client:
        reply = client.post(f'/api/orders/{identifier}/open-folder')
    assert reply.status_code == (401 if role is None else 200)
    assert calls == ([] if role is None else [(r'R:\0. PROJECTS', folder)])


def test_open_order_csrf_missing_folder_unknown_and_disabled_adapter(order_desktop):
    case, calls, identifier, _ = order_desktop
    route = f'/api/orders/{identifier}/open-folder'
    with case.client('ADMIN') as client:
        token = client.headers.pop('X-CSRF-Token')
        assert client.post(route).status_code == 403
        client.headers['X-CSRF-Token'] = token
        assert client.get(route).status_code == 405
        assert client.post('/api/orders/00000000-0000-0000-0000-000000000001/open-folder').status_code == 404
        with case.database.session() as session:
            session.get(Project, identifier).folder_path = None
            session.commit()
        assert client.post(route).status_code == 409
    case.app = create_app(case.app.state.settings, case.database, case.worker)
    with case.client('ADMIN') as client:
        assert client.post(route).status_code == 503
    assert not calls


def test_open_order_is_loopback_only_and_masks_private_failures(order_desktop, monkeypatch):
    case, calls, identifier, _ = order_desktop
    route = f'/api/orders/{identifier}/open-folder'
    with TestClient(case.app, base_url=ORIGIN, client=('192.168.1.2', 50000)) as client:
        login = client.post('/api/auth/login', json={'email': case.users['ADMIN'].email, 'password': PASSWORD}, headers={'Origin': ORIGIN})
        assert login.status_code == 200
        reply = client.post(route, headers={'Origin': ORIGIN, 'X-CSRF-Token': login.json()['csrf_token'], 'X-Forwarded-For': '127.0.0.1'})
        assert reply.status_code == 403
    def fail(*args): raise LocalFilesError('PRIVATE_SOURCE_INFO')
    monkeypatch.setattr('app.main.open_order_folder', fail)
    case.app = create_app(case.app.state.settings, case.database, case.worker, local_library=LocalLibraryStub())
    with case.client('ADMIN') as client:
        reply = client.post(route)
        assert reply.status_code == 409
        assert 'PRIVATE' not in reply.text
    assert not calls


def test_native_open_order_validates_root_and_child_before_launch(tmp_path, monkeypatch):
    import os
    from app.api.order_desktop import open_order_folder
    from app.order_folders import OrderFolderError
    if os.name != 'nt': pytest.skip('Native Windows capability')
    root = tmp_path / 'Orders'; root.mkdir()
    child = root / '0001_SYNTHETIC_SCANNING_102026'; child.mkdir()
    calls = []
    monkeypatch.setattr('app.api.order_desktop.subprocess.Popen', lambda args, **kwargs: calls.append(args))
    open_order_folder(str(root), str(child))
    assert len(calls) == 1 and calls[0][1] == str(child)
    for candidate in [str(root), str(tmp_path / child.name), 'https://example.invalid', str(root / '..' / child.name)]:
        with pytest.raises((OrderFolderError, LocalFilesError, ValueError, OSError)):
            open_order_folder(str(root), candidate)
    assert len(calls) == 1
