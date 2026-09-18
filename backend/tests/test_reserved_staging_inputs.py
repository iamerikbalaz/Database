"""A reservation rechecks its own immutable context without bypassing other owners."""
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import select, update

from app.auth.access import ApplicationAccess
from app.auth.service import AuthContext
from app.db.models import (AuthSession, InternalUser, MaterialAuditEvent, MaterialPackagingExecution,
    PBRMaterial, PublicationStagingJob, UserCredential)
from app.material_identity import require_material_idle, require_folder_idle
from app.packaging_jobs import current_inputs
from app.publication_staging import reserved_staging
from test_application_access import access_case
from test_material_approvals import approval_case
from test_packaging_actions import action_case
from test_packaging_downloads import download_case
from test_publication_staging import staging_case
from test_staging_reservations import PATH, reservation, close_payload


def active_job(item, client):
    response = client.post(PATH, json=reservation(item, client))
    assert response.status_code == 201, response.json()
    return response.json()


def access_for(session, identifier):
    job = session.get(PublicationStagingJob, UUID(identifier))
    return ApplicationAccess(AuthContext(user=session.get(InternalUser, job.actor_id),
        session=session.get(AuthSession, job.issuer_session_id)))


def current(item, identifier, settings=None):
    with item.case.database.session() as session:
        return reserved_staging(session, UUID(identifier), access_for(session, identifier),
            settings or item.case.app.state.settings)


def test_active_job_can_recheck_exact_inputs_without_releasing_owners_or_doing_io(staging_case):
    item = staging_case
    with item.case.client("ADMIN") as client:
        saved = active_job(item, client)
        with item.case.database.session() as session:
            before = len(list(session.scalars(select(MaterialAuditEvent))))
        prepared = current(item, saved["id"])
        assert current(item, saved["id"]) == prepared
        assert prepared.plan.sha256 == saved["plan_sha256"]
        assert len(prepared.packages) == saved["material_count"] == 1
        assert prepared.packages[0].result.stored.proof_sha256 == saved["materials"][0]["packaging_proof_sha256"]
        assert prepared.csv_bytes == client.get("/api/publication-batches/" + saved["batch_id"] + "/csv").content
        assert client.patch(item.material_path, json={"material_name": "Still blocked"}).status_code == 409
        # The public preview's proposed UUID is not an ownership exemption.
        assert client.post(item.staging_path, json=item.staging_payload).status_code == 409
        with item.case.database.session() as session:
            assert len(list(session.scalars(select(MaterialAuditEvent)))) == before
            package = session.get(MaterialPackagingExecution, UUID(item.saved["id"]))
            with pytest.raises(HTTPException) as blocked:
                current_inputs(session, package, access_for(session, saved["id"]))
            assert blocked.value.status_code == 409
    assert item.download.opened == 0 and len(item.worker.commands) == 1 and len(item.inventory.calls) == 2


def test_closed_job_never_borrows_released_ownership(staging_case):
    item = staging_case
    with item.case.client("ADMIN") as client:
        saved = active_job(item, client)
        assert client.post(PATH + "/" + saved["id"] + "/close", json=close_payload(saved)).status_code == 200
        with pytest.raises(HTTPException) as blocked: current(item, saved["id"])
        assert blocked.value.status_code == 409 and blocked.value.detail["code"] == "GCS_STAGING_ALREADY_CLOSED"
        with item.case.database.session() as session:
            with pytest.raises(HTTPException) as blocked:
                require_material_idle(session, item.material.id, staging_job_id=UUID(saved["id"]))
            assert blocked.value.detail["code"] == "GCS_STAGING_OWNERSHIP_CHANGED"


def test_only_the_exact_active_job_can_exempt_its_material_and_source_tree(staging_case):
    item = staging_case
    with item.case.client("ADMIN") as client:
        saved = active_job(item, client)
    with item.case.database.session() as session:
        require_material_idle(session, item.material.id, staging_job_id=UUID(saved["id"]))
        package = session.get(MaterialPackagingExecution, UUID(item.saved["id"]))
        require_folder_idle(session, package.folder_path, staging_job_id=UUID(saved["id"]))
        with pytest.raises(HTTPException): require_material_idle(session, item.material.id, staging_job_id=uuid4())
        with pytest.raises(HTTPException): require_material_idle(session, item.case.materials[1].id, staging_job_id=UUID(saved["id"]))
        with pytest.raises(HTTPException): require_folder_idle(session, package.folder_path, staging_job_id=uuid4())


@pytest.mark.parametrize("change", ["demote", "disable", "password", "revoke", "material", "target", "prefix", "history"])
def test_current_inputs_fail_closed_after_authority_context_or_destination_changes(staging_case, change):
    from datetime import datetime, UTC
    item = staging_case
    with item.case.client("ADMIN") as client:
        saved = active_job(item, client)
    settings = item.case.app.state.settings
    with item.case.database.session() as session:
        job = session.get(PublicationStagingJob, UUID(saved["id"]))
        if change == "demote": session.get(InternalUser, job.actor_id).role = "PROCESSOR"
        elif change == "disable": session.get(InternalUser, job.actor_id).is_active = False
        elif change == "password": session.get(UserCredential, job.actor_id).must_change_password = True
        elif change == "revoke": session.get(AuthSession, job.issuer_session_id).revoked_at = datetime.now(UTC)
        elif change == "material": session.get(PBRMaterial, item.material.id).material_name = "Out-of-band change"
        elif change == "target": settings = settings.model_copy(update={"gcs_bucket_name": "another-staging-bucket"})
        elif change == "prefix": settings = settings.model_copy(update={"gcs_staging_prefix": "another/prefix"})
        elif change == "history":
            # Simulate corrupted persisted evidence in this isolated SQLite DB;
            # deployment append-only PostgreSQL guards are tested separately.
            session.execute(update(PublicationStagingJob).where(PublicationStagingJob.id == job.id).values(plan={}))
        session.commit()
    with pytest.raises(HTTPException) as blocked: current(item, saved["id"], settings)
    expected = {"demote": 403, "disable": 401, "password": 403, "revoke": 401, "history": 503}.get(change, 409)
    assert blocked.value.status_code == expected
    assert item.download.opened == 0 and len(item.worker.commands) == 1 and len(item.inventory.calls) == 2
