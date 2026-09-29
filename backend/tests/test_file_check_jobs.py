"""Resumable local scans: full-scope authorization, atomic results and safe progress."""
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import AuthSession, MaterialAuditEvent, PBRMaterial
from app.file_check_jobs import FileCheckJobs, RETENTION_SECONDS, MAX_JOBS
from test_application_access import ORIGIN, PASSWORD, access_case
from test_automatic_file_check import selected
from test_local_files_api import local_case

URL = "/api/materials/check-jobs"


@pytest.fixture
def jobs_case(local_case, monkeypatch):
    case, library, _, _ = local_case
    queued = []
    monkeypatch.setattr(FileCheckJobs, "_launch", lambda self, job, access: queued.append((self, job, access)))
    library.file_check_profile = "PBR_FILES_V1"
    def check(folders, *, progress):
        library.calls.append(folders)
        progress(progress_value(len(folders)))
        if library.callback:
            library.callback()
        return [{"profile": "PBR_FILES_V1", "complete": True, "report": "PRIVATE_CHECK_REPORT", "issues": []} for _ in folders]
    library.check_many = check
    return case, library, queued


def progress_value(total=1, **changes):
    return {"schema_version": 2, "total": total, "completed": 0, "cache_hits": 3, "cache_misses": 2,
        "active": [{"material_index": 1, "file": "8K/SAFE_COL_8K.jpg", "phase": "DECODING"}], **changes}


def start(client, choices, key=None, **kwargs):
    return client.post(URL, json={"materials": choices, "open_report": False},
        headers={"Idempotency-Key": str(key or uuid4())}, **kwargs)


def execute(queued):
    manager, job, access = queued[-1]
    manager._execute(job, access)
    return manager, job


def test_job_progress_results_and_actor_preserve_human_status(jobs_case):
    case, library, queued = jobs_case
    choices = selected(case)
    with case.database.session() as session:
        material = session.get(PBRMaterial, case.materials[0].id)
        before = material.workflow_status, material.checked_status, material.validation_status
    with case.client("PROCESSOR") as client:
        response = start(client, choices)
        assert response.status_code == 202, response.text
        initial = response.json()
        assert initial["status"] == "RUNNING" and initial["completed"] == 0
        manager, job, _ = queued[0]
        manager._progress(job, progress_value())
        progress = client.get(URL + "/" + initial["id"]).json()
        assert progress["active"] == [{"material_id": choices[0]["id"], "identity": case.materials[0].technical_identity,
            "file": "8K/SAFE_COL_8K.jpg", "phase": "DECODING"}]
        assert progress["cache_hits"] == 3 and progress["result"] is None
        execute(queued)
        response = client.get(URL + "/" + initial["id"])
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        final = response.json()
        assert final["status"] == "COMPLETED" and final["completed"] == final["total"] == 1
        assert final["active"] == [] and final["elapsed_seconds"] >= 0
        assert final["result"]["items"][0]["status"] == "OK"
        assert final["result"]["report_path"] is None
        assert "PRIVATE_CHECK_REPORT" not in final["result"]["report"]  # clean material omitted
    with case.database.session() as session:
        material = session.get(PBRMaterial, case.materials[0].id)
        assert (material.workflow_status, material.checked_status, material.validation_status) == before
        event = session.scalar(select(MaterialAuditEvent))
        assert event.actor_id == case.users["PROCESSOR"].id


def test_same_key_resumes_running_and_completed_scan_without_duplicate_work(jobs_case):
    case, library, queued = jobs_case
    choices = selected(case); key = uuid4()
    with case.client("ADMIN") as client:
        first = start(client, choices, key).json()
        assert start(client, choices, key).json()["id"] == first["id"]
        assert len(queued) == 1 and not library.calls
        busy = start(client, choices)
        assert busy.status_code == 409 and busy.json()["detail"]["code"] == "FILE_CHECK_BUSY"
        conflict = client.post(URL, json={"materials": choices, "open_report": True}, headers={"Idempotency-Key": str(key)})
        assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "FILE_CHECK_REQUEST_CONFLICT"
        execute(queued)
        retry = start(client, choices, key)
        assert retry.status_code == 202 and retry.json()["status"] == "COMPLETED"
        assert retry.json()["id"] == first["id"] and len(queued) == len(library.calls) == 1


@pytest.mark.parametrize("variant,status", [("stale", 409), ("foreign", 404), ("missing_version", 422), ("duplicate", 422), ("empty", 422)])
def test_authorization_and_versions_precede_background_launch(jobs_case, variant, status):
    case, library, queued = jobs_case
    choices = selected(case, both=variant == "foreign")
    if variant == "stale": choices[0]["expected_updated_at"] = "2000-01-01T00:00:00Z"
    if variant == "missing_version": choices[0].pop("expected_updated_at")
    if variant == "duplicate": choices *= 2
    if variant == "empty": choices = []
    with case.client("PROCESSOR") as client:
        response = start(client, choices)
        assert response.status_code == status, response.text
    assert not queued and not library.calls


def test_jobs_are_owner_only_and_every_poll_and_retry_reauthorizes_selection(jobs_case):
    case, library, queued = jobs_case
    choices = selected(case); key = uuid4()
    with case.client("PROCESSOR") as client:
        initial = start(client, choices, key).json()
        with case.client("ADMIN") as other:
            assert other.get(URL + "/" + initial["id"]).status_code == 404
        execute(queued)
        assert client.get(URL + "/" + initial["id"]).status_code == 200
        with case.database.session() as session:
            session.get(PBRMaterial, case.materials[0].id).assigned_processor_id = case.users["OTHER"].id
            session.commit()
        for response in [client.get(URL + "/" + initial["id"]), start(client, choices, key)]:
            assert response.status_code == 404 and "PRIVATE_CHECK_REPORT" not in response.text


@pytest.mark.parametrize("change", ["version", "revocation"])
def test_changes_during_check_cannot_commit_partial_results(jobs_case, change):
    case, library, queued = jobs_case
    choices = selected(case, both=True)
    def changed():
        with case.database.session() as session:
            if change == "version":
                session.get(PBRMaterial, case.materials[0].id).updated_at += timedelta(seconds=1)
            else:
                for auth in session.scalars(select(AuthSession)):
                    auth.revoked_at = auth.created_at
            session.commit()
    library.callback = changed
    with case.client("ADMIN") as client:
        assert start(client, choices).status_code == 202
        _, job = execute(queued)
        assert job.status == "FAILED" and job.result is None
        assert job.error["code"] == ("LOCAL_MATERIAL_CHANGED" if change == "version" else "FILE_CHECK_ACCESS_CHANGED")
    with case.database.session() as session:
        assert all(item.automatic_file_checked_at is None for item in session.scalars(select(PBRMaterial)))
        assert not session.scalars(select(MaterialAuditEvent)).all()


@pytest.mark.parametrize("value", [progress_value(total=2), progress_value(completed=True), progress_value(cache_hits=-1),
    progress_value(active=[{"material_index": 2, "file": "ok.png", "phase": "CHECKING"}]),
    progress_value(active=[{"material_index": 1, "file": "C:/PRIVATE.png", "phase": "CHECKING"}]),
    progress_value(active=[{"material_index": 1, "file": "../PRIVATE.png", "phase": "CHECKING"}]),
    progress_value(active=[{"material_index": 1, "file": "safe.png", "phase": "PRIVATE_ERROR"}]),
    progress_value(active=[None]), None])
def test_untrusted_progress_does_not_escape_into_api_or_approve_material(jobs_case, value):
    case, _, queued = jobs_case
    with case.client("ADMIN") as client:
        initial = start(client, selected(case)).json()
        manager, job, _ = queued[0]
        manager._progress(job, value)
        state = client.get(URL + "/" + initial["id"]).json()
        assert state["completed"] == state["cache_hits"] == state["cache_misses"] == 0
        assert state["active"] == [] and state["result"] is None and state["status"] == "RUNNING"


def test_failures_are_safe_and_never_expose_exception_paths(jobs_case):
    case, library, queued = jobs_case
    def fail(*args, **kwargs): raise RuntimeError("C:/SECRET/cache-key/private.key")
    library.check_many = fail
    with case.client("ADMIN") as client:
        initial = start(client, selected(case)).json()
        execute(queued)
        response = client.get(URL + "/" + initial["id"])
        assert response.json()["status"] == "FAILED" and "SECRET" not in response.text
        assert response.json()["result"] is None


def test_post_requires_csrf_and_uuid_key_and_poll_requires_loopback(jobs_case):
    case, _, queued = jobs_case
    choices = selected(case)
    with case.client("ADMIN") as client:
        assert client.post(URL, json={"materials": choices}).status_code == 422
        assert start(client, choices, "invalid").status_code == 422
        initial = start(client, choices).json()
        client.headers.pop("X-CSRF-Token")
        assert start(client, choices).status_code == 403
    with TestClient(case.app, base_url=ORIGIN, client=("192.168.1.20", 50000)) as client:
        login = client.post("/api/auth/login", json={"email": case.users["ADMIN"].email, "password": PASSWORD}, headers={"Origin": ORIGIN})
        client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"], "X-Forwarded-For": "127.0.0.1"})
        assert client.get(URL + "/" + initial["id"]).status_code == 403
        assert start(client, choices).status_code == 403
    assert len(queued) == 1


def test_expired_jobs_do_not_silently_restart_and_storage_is_bounded(jobs_case):
    case, library, queued = jobs_case
    with case.client("ADMIN") as client:
        choices = selected(case); key = uuid4()
        initial = start(client, choices, key).json()
        manager, job = execute(queued)
        job.finished -= RETENTION_SECONDS + 1
        assert client.get(URL + "/" + initial["id"]).status_code == 404
        assert start(client, choices, key).status_code == 404
        assert len(queued) == 1
        for _ in range(MAX_JOBS + 1):
            assert start(client, selected(case)).status_code == 202
            execute(queued)
        assert len(manager.jobs) == MAX_JOBS


def test_evicted_failed_job_key_cannot_restart_scan_with_unchanged_versions(jobs_case):
    case, library, queued = jobs_case
    def fail(*args, **kwargs): raise RuntimeError("unavailable")
    library.check_many = fail
    choices = selected(case); key = uuid4()
    with case.client("ADMIN") as client:
        first = start(client, choices, key).json()
        manager, _ = execute(queued)
        for _ in range(MAX_JOBS):
            assert start(client, choices).status_code == 202
            execute(queued)
        assert first["id"] not in manager.jobs
        response = start(client, choices, key)
        assert response.status_code == 404 and len(queued) == MAX_JOBS + 1
