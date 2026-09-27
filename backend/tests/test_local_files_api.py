"""Desktop API boundaries tested without opening a picker or touching local files."""
from datetime import timedelta

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from app.db.models import AuthSession, InternalUser, PBRMaterial, UserCredential
from app.local_filesystem import LocalFilesError
from app.main import create_app
from test_application_access import ORIGIN, PASSWORD, access_case


ROUTES = (
    ("GET", "/local-files", "absolute"),
    ("POST", "/local-files/open-folder", "open_folder"),
    ("POST", "/local-files/open-metadata", "open_metadata"),
    ("POST", "/local-files/select-destination", "select_destination"),
    ("POST", "/check-data", "check"),
)


class LocalLibraryStub:
    def __init__(self):
        self.calls = []
        self.callback = None
        self.failure = False

    def invoke(self, action, folder=None):
        self.calls.append((action, folder))
        if self.callback:
            self.callback()
        if self.failure:
            raise LocalFilesError("PRIVATE_LOCAL_FAILURE")
        return {
            "absolute": "C:\\Synthetic\\Library\\Material",
            "select_destination": {"destination_path": "C:\\Synthetic\\Destination", "target_parent": "Destination"},
            "check": {"report": "PRIVATE_CHECK_REPORT", "issues": []},
        }.get(action)

    def absolute(self, folder): return self.invoke("absolute", folder)
    def open_folder(self, folder): return self.invoke("open_folder", folder)
    def open_metadata(self, folder): return self.invoke("open_metadata", folder)
    def select_destination(self): return self.invoke("select_destination")
    def check(self, folder): return self.invoke("check", folder)


@pytest.fixture
def local_case(access_case):
    case = access_case
    library = LocalLibraryStub()
    case.app = create_app(case.app.state.settings, case.database, case.worker, local_library=library)
    with case.database.session() as session:
        material = session.get(PBRMaterial, case.materials[0].id)
        material.folder_path = "Library/" + material.technical_identity
        session.commit()
        folder = material.folder_path
    return case, library, f"/api/materials/{material.id}", folder


@pytest.mark.parametrize("role", [None, "OTHER", "PROCESSOR", "LEADERSHIP", "PRODUCTION_LEAD", "ADMIN"])
def test_local_routes_use_authenticated_material_scope(local_case, role):
    case, library, url, folder = local_case
    with case.client(role) as client:
        for method, suffix, action in ROUTES:
            library.calls.clear()
            response = client.request(method, url + suffix)
            expected = 401 if role is None else 403 if action == "select_destination" and role not in {"ADMIN", "PRODUCTION_LEAD"} else 404 if role == "OTHER" else 200
            assert response.status_code == expected, (role, suffix, response.text)
            assert response.headers["cache-control"] == "no-store"
            if expected != 200:
                assert not library.calls
            else:
                assert library.calls == [(action, None if action == "select_destination" else folder)]
                if action == "absolute":
                    assert response.json() == {"absolute_path": "C:\\Synthetic\\Library\\Material", "can_open": True,
                        "can_move": role in {"ADMIN", "PRODUCTION_LEAD"}}


def test_desktop_actions_require_post_csrf_and_trusted_origin(local_case):
    case, library, url, _ = local_case
    with case.client("ADMIN") as client:
        token = client.headers.pop("X-CSRF-Token")
        for method, suffix, _ in ROUTES:
            if method != "POST":
                continue
            assert client.get(url + suffix).status_code == 405
            assert client.post(url + suffix).status_code == 403
            assert client.post(url + suffix, headers={"X-CSRF-Token": token, "Origin": "https://untrusted.invalid"}).status_code == 403
            assert client.post(url + suffix, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert not library.calls


@pytest.mark.parametrize("host", ["198.51.100.12", "192.168.1.20", "::ffff:192.168.1.20"])
def test_remote_clients_cannot_use_local_desktop_even_with_forwarded_loopback(local_case, host):
    case, library, url, _ = local_case
    with TestClient(case.app, base_url=ORIGIN, client=(host, 50000)) as client:
        login = client.post("/api/auth/login", json={"email": case.users["ADMIN"].email, "password": PASSWORD}, headers={"Origin": ORIGIN})
        assert login.status_code == 200
        client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"], "X-Forwarded-For": "127.0.0.1"})
        for method, suffix, _ in ROUTES:
            response = client.request(method, url + suffix)
            assert response.status_code == 403
            assert response.json()["detail"]["code"] == "LOCAL_DESKTOP_ONLY"
    assert not library.calls


@pytest.mark.parametrize("action,role", [("check", "PROCESSOR"), ("select_destination", "PRODUCTION_LEAD")])
@pytest.mark.parametrize("change,expected", [("disabled", 401), ("password", 403), ("revoked", 401), ("scope", None), ("folder", 409), ("version", 409)])
@pytest.mark.parametrize("failure", [False, True])
def test_long_local_actions_reauthorize_before_returning_data_or_failure(local_case, action, role, change, expected, failure):
    case, library, url, _ = local_case
    def during_io():
        with case.database.session() as session:
            user = session.get(InternalUser, case.users[role].id)
            material = session.get(PBRMaterial, case.materials[0].id)
            if change == "disabled": user.is_active = False
            elif change == "password": session.get(UserCredential, user.id).must_change_password = True
            elif change == "revoked":
                for stored in session.scalars(select(AuthSession).where(AuthSession.user_id == user.id)):
                    stored.revoked_at = stored.created_at
            elif change == "scope":
                if role == "PROCESSOR": material.assigned_processor_id = case.users["OTHER"].id
                else: user.role = "LEADERSHIP"
            elif change == "folder": material.folder_path = "Changed/" + material.technical_identity
            else: material.updated_at += timedelta(seconds=1)
            session.commit()
    library.callback = during_io
    library.failure = failure
    suffix = next(suffix for _, suffix, name in ROUTES if name == action)
    with case.client(role) as client:
        response = client.post(url + suffix)
        status = expected if expected is not None else 404 if role == "PROCESSOR" else 403
        assert response.status_code == status, response.text
        assert "PRIVATE" not in response.text and "Synthetic" not in response.text and "Destination" not in response.text
    assert len(library.calls) == 1


def test_local_actions_only_receive_the_stored_material_path(local_case):
    case, library, url, folder = local_case
    with case.client("ADMIN") as client:
        for method, suffix, action in ROUTES:
            library.calls.clear()
            response = client.request(method, url + suffix, params={"path": "C:/Private"}, json={"folder_path": "C:/Private", "root": "C:/Private"})
            assert response.status_code == 200
            assert library.calls == [(action, None if action == "select_destination" else folder)]


def test_local_failures_are_redacted(local_case):
    case, library, url, _ = local_case
    library.failure = True
    with case.client("ADMIN") as client:
        for method, suffix, _ in ROUTES:
            response = client.request(method, url + suffix)
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "LOCAL_FILE_ACTION_FAILED"
            assert "PRIVATE" not in response.text


def test_uninstalled_local_capability_is_reported_without_disclosing_other_materials(local_case):
    case, _, url, _ = local_case
    case.app = create_app(case.app.state.settings, case.database, case.worker)
    with case.client("PROCESSOR") as client:
        response = client.get(url + "/local-files")
        assert response.status_code == 200
        assert response.json() == {"absolute_path": None, "can_open": False, "can_move": False}
        for _, suffix, _ in ROUTES[1:]:
            response = client.post(url + suffix)
            assert response.status_code == 503
            assert response.json()["detail"]["code"] == "LOCAL_DESKTOP_UNAVAILABLE"
    with case.client("OTHER") as client:
        assert client.get(url + "/local-files").status_code == 404
    with case.client() as client:
        assert client.get(url + "/local-files").status_code == 401


@pytest.mark.parametrize("installed", [False, True])
def test_unlinked_material_reports_disabled_controls_without_starting_local_actions(local_case, installed):
    case, library, url, _ = local_case
    if not installed:
        case.app = create_app(case.app.state.settings, case.database, case.worker)
    with case.database.session() as session:
        session.get(PBRMaterial, case.materials[0].id).folder_path = None
        session.commit()
    with case.client("ADMIN") as client:
        response = client.get(url + "/local-files")
        assert response.status_code == 200
        assert response.json() == {"absolute_path": None, "can_open": False, "can_move": False}
        for _, suffix, _ in ROUTES[1:]:
            response = client.post(url + suffix)
            assert response.status_code == (409 if installed else 503)
    assert not library.calls
