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
    regular_file = root / '0002_NOT_A_DIRECTORY'; regular_file.write_text('synthetic')
    for candidate in [str(root), str(tmp_path / child.name), 'https://example.invalid', str(root / '..' / child.name),
                      str(root / '0003_MISSING'), str(regular_file)]:
        with pytest.raises((OrderFolderError, LocalFilesError, ValueError, OSError)):
            open_order_folder(str(root), candidate)
    assert len(calls) == 1


@pytest.fixture
def mapped_order_handles(monkeypatch):
    """Model Windows resolving a mapped drive to the same UNC tree as Explorer."""
    import ctypes
    import os
    from pathlib import Path
    from types import SimpleNamespace
    from app.api import order_desktop
    if os.name != 'nt': pytest.skip('Native Windows capability')

    def install(root, *, reparse=None, redirected=None, unavailable=None):
        root = Path(root)
        canonical_anchor = Path(r'\\nas.example.invalid\projects')
        opened = []; closed = []; paths = {}

        def create_file(path, access, sharing, security, disposition, flags, template):
            assert access == 0x80 and sharing == 3 and disposition == 3 and flags == 0x02200000
            path = Path(path)
            if path == unavailable: return ctypes.c_void_p(-1).value
            handle = len(opened) + 1
            paths[handle] = path; opened.append(handle)
            return handle

        def information(handle, pointer):
            info = ctypes.cast(pointer, ctypes.POINTER(order_desktop.FileInfo)).contents
            info.attrs = 0x10 | (0x400 if paths[handle] == reparse else 0)
            return 1

        def final_name(handle, buffer, size, flags):
            path = paths[handle]
            canonical = canonical_anchor.joinpath(*path.parts[1:])
            if path == redirected: canonical = Path(r'\\other.example.invalid\outside') / path.name
            buffer.value = '\\\\?\\UNC\\' + str(canonical)[2:]
            return len(buffer.value)

        kernel = SimpleNamespace(CreateFileW=create_file, GetFileInformationByHandle=information,
            GetFinalPathNameByHandleW=final_name, CloseHandle=lambda handle: closed.append(handle))
        monkeypatch.setattr(order_desktop.ctypes, 'WinDLL', lambda *args, **kwargs: kernel)
        monkeypatch.setattr(order_desktop, 'checked_root', lambda value: Path(value))
        monkeypatch.setenv('WINDIR', r'C:\Windows')
        return canonical_anchor.joinpath(*root.parts[1:]), opened, closed
    return install


@pytest.mark.parametrize('root', [r'R:\0. PROJECTS', r'\\nas.example.invalid\projects\0. PROJECTS'])
def test_open_mapped_or_unc_order_uses_verified_destination_with_handles_held(mapped_order_handles, monkeypatch, root):
    from pathlib import Path
    from app.api.order_desktop import open_order_folder
    canonical_root, opened, closed = mapped_order_handles(root)
    name = '0143_SYNTHETIC_SCANNING_FABRICS_112024'
    calls = []

    def launch(args, **kwargs):
        assert opened and not closed
        assert kwargs == {'close_fds': True}
        calls.append(args)

    monkeypatch.setattr('app.api.order_desktop.subprocess.Popen', launch)
    open_order_folder(root, str(Path(root) / name))
    assert calls == [[r'C:\Windows\explorer.exe', str(canonical_root / name)]]
    assert closed == list(reversed(opened))


@pytest.mark.parametrize('failure', ['reparse', 'redirected', 'unavailable'])
@pytest.mark.parametrize('level', ['root', 'child'])
def test_open_mapped_order_rejects_unsafe_or_missing_directory(mapped_order_handles, monkeypatch, failure, level):
    from pathlib import Path
    from app.api.order_desktop import open_order_folder
    root = Path(r'R:\0. PROJECTS')
    child = root / '0143_SYNTHETIC_SCANNING_FABRICS_112024'
    _, opened, closed = mapped_order_handles(str(root), **{failure: root if level == 'root' else child})
    calls = []
    monkeypatch.setattr('app.api.order_desktop.subprocess.Popen', lambda *args, **kwargs: calls.append(args))
    with pytest.raises(LocalFilesError):
        open_order_folder(str(root), str(child))
    assert not calls
    assert closed == list(reversed(opened))


def test_open_mapped_order_closes_handles_when_launch_fails(mapped_order_handles, monkeypatch):
    from pathlib import Path
    from app.api.order_desktop import open_order_folder
    root = r'R:\0. PROJECTS'
    _, opened, closed = mapped_order_handles(root)

    def fail(*args, **kwargs): raise OSError('Synthetic launch failure')

    monkeypatch.setattr('app.api.order_desktop.subprocess.Popen', fail)
    with pytest.raises(OSError):
        open_order_folder(root, str(Path(root) / '0143_SYNTHETIC_SCANNING_FABRICS_112024'))
    assert closed == list(reversed(opened))
