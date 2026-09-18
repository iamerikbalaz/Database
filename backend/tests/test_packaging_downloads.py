"""Actual sessions/DB authorization with explicitly synthetic artifact streams."""
from contextlib import asynccontextmanager
import hashlib
import traceback
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.api import packaging_downloads as api
from app.db.models import InternalUser, UserCredential
from app.db.errors import DatabaseResponseInterrupted
from app.packaging_client import PackagingClientError
from test_application_access import access_case
from test_material_approvals import approval_case
from test_packaging_actions import action_case, act

DATA = b"Synthetic preview preserved"
FILE_PATH = "PREVIEW/preview.png"


class DownloadStub:
    def __init__(self):
        self.opened = 0; self.closed = 0; self.callback = None; self.during = None
        self.failure = None; self.data = DATA; self.mismatch = False
    @asynccontextmanager
    async def open(self, prepared, report, result, path):
        self.opened += 1
        if self.callback: self.callback()
        if self.failure: raise self.failure
        item = next(item for item in result.stored.payload.files if item.path == path)
        async def chunks():
            for start in range(0, len(self.data), 4):
                if self.during: self.during()
                yield self.data[start:start + 4]
        try:
            yield SimpleNamespace(file=item, proof_sha256="b"*64 if self.mismatch else result.stored.proof_sha256, chunks=chunks)
        finally: self.closed += 1


@pytest.fixture
def download_case(action_case):
    item = action_case
    with item.case.client("ADMIN") as client:
        assert act(item, client).json()["status"] == "PACKAGED"
    item.download = DownloadStub()
    item.worker.open_artifact = item.download.open
    item.files_path = item.job_path + "/artifacts"
    with item.case.client("ADMIN") as client:
        response = client.get(item.files_path)
        assert response.status_code == 200, response.json()
        item.files = response.json()
        item.file = next(file for file in item.files["items"] if file["path"] == FILE_PATH)
        assert item.file["size"] == len(DATA) and item.file["sha256"] == hashlib.sha256(DATA).hexdigest()
    item.download_path = item.files_path + "/" + item.file["id"]
    item.params = {"proof_sha256": item.files["proof_sha256"]}
    return item


def test_list_pages_and_download_remain_historical_after_material_edit(download_case):
    item = download_case
    with item.case.client("ADMIN") as client:
        assert client.patch(item.material_path, json={"material_name":"Changed after packaging"}).status_code == 200
        first = client.get(item.files_path, params={"limit":1}).json()
        second = client.get(item.files_path, params={"after":first["next_cursor"], "limit":50}).json()
        assert first["items"] + second["items"] == item.files["items"] and second["next_cursor"] is None
        response = client.get(item.download_path, params=item.params)
        assert response.status_code == 200 and response.content == DATA
        assert response.headers["content-length"] == str(len(DATA))
        assert "filename*=UTF-8''preview.png" in response.headers["content-disposition"]
        assert response.headers["cache-control"] == "no-store" and response.headers["x-content-type-options"] == "nosniff"
        assert "folder_path" not in client.get(item.files_path).text and "worker_request" not in client.get(item.files_path).text
        assert client.get(item.material_path).json()["is_published"] is False
    assert item.download.opened == item.download.closed == 1
    assert len(item.worker.commands) == 1 and len(item.inventory.calls) == 2


@pytest.mark.parametrize("failure_at", [1, 2, 3])
def test_database_failure_during_download_does_not_expose_driver_details(download_case, monkeypatch, caplog, failure_at):
    item = download_case
    marker = uuid4().hex
    calls = 0
    original = api.AuthorizedArtifactResponse.authorize

    def authorize(response):
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise OperationalError(marker, {"value": marker}, RuntimeError(marker))
        return original(response)

    monkeypatch.setattr(api.AuthorizedArtifactResponse, "authorize", authorize)
    monkeypatch.setattr(api, "RECHECK_BYTES", 4)
    with item.case.client("LEADERSHIP") as client:
        if failure_at == 3:
            with pytest.raises(DatabaseResponseInterrupted) as captured:
                client.get(item.download_path, params=item.params)
            assert marker not in "".join(traceback.format_exception(captured.value))
        else:
            response = client.get(item.download_path, params=item.params)
            assert response.status_code == 503
            assert response.json() == {"detail": {"code": "DATABASE_UNAVAILABLE"}}
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert marker not in response.text
    assert item.download.opened == item.download.closed == (0 if failure_at == 1 else 1)
    assert marker not in caplog.text
    records = [record for record in caplog.records if record.name == "reawote.database"]
    assert len(records) == 1 and records[0].exc_info is None
    assert len(item.worker.commands) == 1 and len(item.inventory.calls) == 2


@pytest.mark.parametrize("role,expected", [(None,401), ("PROCESSOR",403), ("PRODUCTION_LEAD",403), ("LEADERSHIP",200)])
def test_operator_role_matrix_before_any_worker_access(download_case, role, expected):
    item = download_case
    with item.case.client(role) as client:
        assert client.get(item.files_path).status_code == expected
        assert client.get(item.download_path, params=item.params).status_code == expected
    assert item.download.opened == (1 if expected == 200 else 0)


@pytest.mark.parametrize("change,expected", [("proof",409), ("file",404), ("range",422), ("scope",404), ("missing-proof",422)])
def test_wrong_proof_file_scope_and_range_fail_before_io(download_case, change, expected):
    item = download_case; path = item.download_path; params = dict(item.params); headers = {}
    if change == "proof": params["proof_sha256"] = "b" * 64
    elif change == "file": path = item.files_path + "/" + "b" * 64
    elif change == "range": headers["Range"] = "bytes=0-4"
    elif change == "scope": path = path.replace(str(item.material.id), str(uuid4()))
    else: params = {}
    with item.case.client("ADMIN") as client: assert client.get(path, params=params, headers=headers).status_code == expected
    assert item.download.opened == 0


def test_unaccepted_reservation_never_exposes_files(action_case):
    item = action_case
    with item.case.client("ADMIN") as client:
        assert client.get(item.job_path + "/artifacts").status_code == 409
        item.worker.ready = False
        assert act(item, client).json()["status"] == "RETRY_REQUIRED"
        assert client.get(item.job_path + "/artifacts").status_code == 409


@pytest.mark.parametrize("change,expected", [("disable",401), ("demote",403), ("password",403), ("worker-error",401)])
def test_account_rechecked_after_open_before_headers_even_on_worker_error(download_case, change, expected):
    item = download_case
    def revoke():
        with item.case.database.session() as session:
            user = session.get(InternalUser, item.case.users["LEADERSHIP"].id)
            if change in {"disable", "worker-error"}: user.is_active = False
            elif change == "demote": user.role = "PROCESSOR"
            else: session.get(UserCredential, user.id).must_change_password = True
            session.commit()
    item.download.callback = revoke
    if change == "worker-error": item.download.failure = PackagingClientError("PACKAGING_STORE_FILE_NOT_FOUND")
    with item.case.client("LEADERSHIP") as client:
        response = client.get(item.download_path, params=item.params)
        assert response.status_code == expected and response.headers["content-type"] == "application/json"
        assert "PACKAGING_STORE" not in response.text and DATA.decode() not in response.text
    assert item.download.closed == (0 if change == "worker-error" else 1)


@pytest.mark.parametrize("change", ["corrupt", "truncate", "oversize", "proof", "revoke"])
def test_stream_is_independently_verified_and_late_revocation_interrupts(download_case, monkeypatch, change):
    item = download_case
    if change == "corrupt": item.download.data = b"!" + DATA[1:]
    elif change == "truncate": item.download.data = DATA[:-1]
    elif change == "oversize": item.download.data = DATA + b"x"
    elif change == "proof": item.download.mismatch = True
    else:
        monkeypatch.setattr(api, "RECHECK_BYTES", 4)
        def revoke():
            with item.case.database.session() as session:
                session.get(InternalUser, item.case.users["LEADERSHIP"].id).is_active = False
                session.commit()
        item.download.during = revoke
    with item.case.client("LEADERSHIP") as client:
        if change == "proof": assert client.get(item.download_path, params=item.params).status_code == 503
        else:
            with pytest.raises(RuntimeError, match="Packaging transfer interrupted"):
                client.get(item.download_path, params=item.params)
    assert item.download.opened == item.download.closed == 1
