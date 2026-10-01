"""Settings selection is a read-only desktop action, never a filesystem grant."""
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from app.db.models import InternalUser
from app.db.path_settings_models import PathsSettingsRevision
from app.local_filesystem import LocalFilesError
from app.main import create_app
from test_application_access import ORIGIN, PASSWORD, access_case  # noqa: F401
from test_path_settings import paths_case  # noqa: F401

ENDPOINT = "/api/settings/paths"


class Picker:
    def __init__(self):
        self.calls = []; self.result = None; self.callback = None; self.error = None

    def select_settings_folder(self, initial, description):
        self.calls.append((initial, description))
        if self.callback: self.callback()
        if self.error: raise self.error
        return self.result


@pytest.fixture
def picker_case(paths_case):
    case, paths = paths_case
    picker = Picker()
    case.app = create_app(case.app.state.settings, case.database, case.worker, local_library=picker)
    return case, paths, picker


@pytest.mark.parametrize("role,status", [(None, 401), ("ADMIN", 200), ("PROCESSOR", 403), ("PRODUCTION_LEAD", 403), ("LEADERSHIP", 403)])
def test_picker_is_admin_only(picker_case, role, status):
    case, _, picker = picker_case
    with case.client(role) as client:
        response = client.post(ENDPOINT + "/select-folder", json={"field": "orders_root"})
        assert response.status_code == status
        assert response.headers["cache-control"] == "no-store"
    assert len(picker.calls) == (1 if status == 200 else 0)


@pytest.mark.parametrize("field,key", [("sbs_templates_root", "templates"), ("orders_root", "orders"), ("materials_root", "materials"), ("published_library_root", "published")])
def test_selection_uses_saved_start_path_and_never_saves_or_grants_writes(picker_case, field, key):
    case, paths, picker = picker_case
    picker.result = str(paths[key])
    with case.client("ADMIN") as client:
        assert client.get(ENDPOINT).json()["can_select_folder"] is True
        response = client.post(ENDPOINT + "/select-folder", json={"field": field})
        assert response.status_code == 200, response.text
        assert response.json() == {"folder_path": str(paths[key])}
        assert picker.calls[0][0] == str(paths[key])
        assert client.get(ENDPOINT).json()["version"] == 0
    with case.database.session() as session:
        assert session.scalar(select(PathsSettingsRevision)) is None


def test_cancel_and_disabled_capability(paths_case, picker_case):
    case, _, picker = picker_case
    with case.client("ADMIN") as client:
        assert client.post(ENDPOINT + "/select-folder", json={"field": "orders_root"}).json() == {"folder_path": None}
        assert client.get(ENDPOINT).json()["version"] == 0
    case.app = create_app(case.app.state.settings, case.database, case.worker)
    with case.client("ADMIN") as client:
        assert client.get(ENDPOINT).json()["can_select_folder"] is False
        assert client.post(ENDPOINT + "/select-folder", json={"field": "orders_root"}).status_code == 503
    assert len(picker.calls) == 1


def test_picker_requires_csrf_origin_and_known_field(picker_case):
    case, _, picker = picker_case
    with case.client("ADMIN") as client:
        token = client.headers.pop("X-CSRF-Token")
        assert client.post(ENDPOINT + "/select-folder", json={"field": "orders_root"}).status_code == 403
        assert client.post(ENDPOINT + "/select-folder", json={"field": "orders_root"}, headers={"X-CSRF-Token": token, "Origin": "https://untrusted.invalid"}).status_code == 403
        client.headers["X-CSRF-Token"] = token
        assert client.post(ENDPOINT + "/select-folder", json={"field": "arbitrary", "path": "C:/private"}).status_code == 422
        assert client.get(ENDPOINT + "/select-folder").status_code == 405
    assert not picker.calls


def test_remote_client_cannot_invoke_picker_using_forwarded_loopback(picker_case):
    case, _, picker = picker_case
    with TestClient(case.app, base_url=ORIGIN, client=("192.168.1.20", 50000)) as client:
        login = client.post("/api/auth/login", json={"email": case.users["ADMIN"].email, "password": PASSWORD}, headers={"Origin": ORIGIN})
        client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"], "X-Forwarded-For": "127.0.0.1"})
        assert client.get(ENDPOINT).json()["can_select_folder"] is False
        response = client.post(ENDPOINT + "/select-folder", json={"field": "orders_root"})
        assert response.status_code == 403 and response.json()["detail"]["code"] == "LOCAL_DESKTOP_ONLY"
    assert not picker.calls


@pytest.mark.parametrize("failure", [False, True])
def test_picker_rechecks_admin_role_after_native_dialog_even_when_it_failed(picker_case, failure):
    case, paths, picker = picker_case
    def during_dialog():
        with case.database.session() as session:
            session.get(InternalUser, case.users["ADMIN"].id).role = "PROCESSOR"
            session.commit()
    picker.callback = during_dialog
    picker.result = str(paths["published"])
    if failure: picker.error = LocalFilesError("PRIVATE_DETAILS")
    with case.client("ADMIN") as client:
        response = client.post(ENDPOINT + "/select-folder", json={"field": "published_library_root"})
        assert response.status_code == 403
        assert "published" not in response.text and "PRIVATE" not in response.text


def test_settings_picker_does_not_expand_test_material_root(picker_case):
    case, paths, picker = picker_case
    picker.result = str(paths["published"])
    with case.client("ADMIN") as client:
        assert client.post(ENDPOINT + "/select-folder", json={"field": "materials_root"}).status_code == 422
        assert client.post(ENDPOINT + "/select-folder", json={"field": "orders_root"}).status_code == 200
        assert client.get(ENDPOINT).json()["materials_root"] == str(paths["materials"])
    assert case.app.state.settings.materials_root == str(paths["materials"])


@pytest.mark.parametrize("code", ["LOCAL_PICKER_BUSY", "LOCAL_PICKER_TIMEOUT", "PRIVATE_DETAILS"])
def test_picker_failure_is_sanitized_and_can_be_retried(picker_case, code):
    case, _, picker = picker_case
    picker.error = LocalFilesError(code)
    with case.client("ADMIN") as client:
        response = client.post(ENDPOINT + "/select-folder", json={"field": "orders_root"})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == (code if code != "PRIVATE_DETAILS" else "LOCAL_PICKER_UNAVAILABLE")
        picker.error = None
        assert client.post(ENDPOINT + "/select-folder", json={"field": "orders_root"}).status_code == 200
        assert client.get(ENDPOINT).json()["version"] == 0
