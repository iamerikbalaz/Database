"""Real application records + bounded synthetic streams, without cloud/NAS writes.

The worker stub supplies synthetic archive bytes with a self-consistent proof.
Actual ZIP production is covered by the separate Linux worker/runtime suite.
"""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import anyio
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import select

from app.api.staging_execution import StagingAction as Action
from app.db.models import (PublicationStagingJob, PublicationStagingDispatch, PublicationStagingTransfer,
    PublicationStagingObservation, PublicationStagingResult, PublicationStagingState,
    InternalUser, UserCredential, AuthSession, PBRMaterial)
from app.gcs_contract import GcsConfiguration, GcsObjectReceipt, GcsError
from app.main import create_app
from app.packaging_contract import DispatchedPackagingResult
from app.staging_runtime import StagingCoordinator
from test_application_access import access_case
from test_material_approvals import approval_case
from test_packaging_actions import action_case, act
from test_packaging_client import rehash
from test_reserved_staging_inputs import access_for
from test_staging_reservations import PATH


class Cloud:
    def __init__(self, configuration, database):
        self.configuration = configuration; self.database = database; self.calls = []
        self.objects = {}; self.before = None; self.after = None; self.failure = None; self.corrupt = None

    async def verify(self, spec, method, guard):
        with self.database.session() as session:
            state = session.get(PublicationStagingState, UUID(spec.job_id))
            intent = session.scalar(select(PublicationStagingTransfer).where(
                PublicationStagingTransfer.dispatch_id == state.last_dispatch_id,
                PublicationStagingTransfer.relative_path == spec.relative_path))
            assert intent is not None and state.status == "RUNNING"
            assert intent.size == spec.size and intent.sha256 == spec.sha256
        self.calls.append((method, spec))
        if self.before: await self.before(spec)
        await guard()
        if self.failure: raise self.failure

    def receipt(self, spec):
        result = GcsObjectReceipt(spec=spec, bucket_name=self.configuration.bucket_name,
            object_name=spec.object_name(self.configuration), generation=str(100 + len(spec.relative_path)), metageneration="1")
        return self.corrupt(result) if self.corrupt else result

    async def upload(self, spec, source, *, operation_guard):
        await self.verify(spec, "upload", operation_guard)
        data = b"".join([block async for block in source])
        assert len(data) == spec.size and hashlib.sha256(data).hexdigest() == spec.sha256
        assert spec.relative_path not in self.objects
        self.objects[spec.relative_path] = data
        if self.after: await self.after(spec)
        await operation_guard()
        return self.receipt(spec)

    async def reconcile(self, spec, *, operation_guard):
        await self.verify(spec, "reconcile", operation_guard)
        data = self.objects.get(spec.relative_path)
        if data is None: raise GcsError("GCS_OBJECT_ABSENT")
        if len(data) != spec.size or hashlib.sha256(data).hexdigest() != spec.sha256: raise GcsError("GCS_VERIFICATION_FAILED")
        if self.after: await self.after(spec)
        await operation_guard()
        return self.receipt(spec)


@pytest.fixture
def runtime_case(action_case):
    return make_runtime_case(action_case)


def make_runtime_case(action_case):
    item = action_case
    original = item.worker.dispatch
    item.bytes = {"PREVIEW/preview.png": b"Synthetic preview preserved",
        "PREVIEW/český náhled.png": b"Synthetic unicode preview",
        "metadata.json": (json.dumps({"WEB_APP_PART": {"TEXTURE_RESOLUTIONS": {"1K": "1024x1024"},
            "IMAGE_RATIO": 1.0, "MAPS_SHORTCUTS": ["COL", "NRM16"]}, "DESKTOP_APP_PART": {}},
            ensure_ascii=True, indent=2) + "\n").encode()}
    def synthetic(prepared, report, command):
        result = original(prepared, report, command).model_dump(mode="json")
        payload = result["stored"]["payload"]
        for archive in payload["bundle"]["archives"]:
            data = b"Explicitly synthetic archive stream: " + archive["filename"].encode()
            item.bytes[archive["filename"]] = data
            archive.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
            record = next(record for record in payload["files"] if record["path"] == archive["filename"])
            record.update(size=archive["size"], sha256=archive["sha256"])
        rehash(result)
        result = DispatchedPackagingResult.model_validate(result); result.verify_request(prepared, report)
        return result
    item.worker.dispatch = synthetic
    item.opened = 0; item.closed = 0; item.open_callback = None
    @asynccontextmanager
    async def opening(prepared, report, result, path):
        item.opened += 1
        file = next(file for file in result.stored.payload.files if file.path == path)
        data = item.bytes[path]
        assert len(data) == file.size and hashlib.sha256(data).hexdigest() == file.sha256
        if item.open_callback: item.open_callback()
        async def chunks():
            for start in range(0, len(data), 8): yield data[start:start + 8]
        try: yield SimpleNamespace(file=file, proof_sha256=result.stored.proof_sha256, chunks=chunks)
        finally: item.closed += 1
    item.worker.open_artifact = opening
    item.case.app = create_app(item.case.app.state.settings, item.case.database, item.case.worker,
        inventory_client=item.inventory, technical_client=item.technical, packaging_client=item.worker)
    with item.case.client("ADMIN") as client:
        packaged = act(item, client)
        assert packaged.status_code == 200 and packaged.json()["status"] == "PACKAGED", packaged.json()
        packaged = packaged.json()
    item.settings = item.case.app.state.settings.model_copy(update=dict(gcs_enabled=True,
        gcs_bucket_name="synthetic-reawote-staging", gcs_staging_prefix="isolated/contracts",
        gcs_access_token=SecretStr("synthetic-" + "x" * 40)))
    item.case.app = create_app(item.settings, item.case.database, item.case.worker,
        inventory_client=item.inventory, technical_client=item.technical, packaging_client=item.worker)
    with item.case.client("ADMIN") as client:
        batch_path = "/api/publication-batches/" + item.batch["id"]
        batch = client.get(batch_path).json()
        preview_body = dict(job_id=str(uuid4()), expected_snapshot_hash=batch["snapshot_hash"],
            expected_csv_sha256=batch["csv_sha256"], packages=[dict(material_id=str(item.material.id),
                execution_id=packaged["id"], expected_observation_id=packaged["last_observation_id"],
                expected_proof_sha256=packaged["proof_sha256"])])
        preview = client.post(batch_path + "/staging-preview", json=preview_body)
        assert preview.status_code == 200, preview.json()
        reserved = client.post(PATH, json=preview_body | dict(batch_id=batch["id"], idempotency_key=str(uuid4()),
            reason="Synthetic runtime reservation", expected_plan_sha256=preview.json()["plan_sha256"]))
        assert reserved.status_code == 201, reserved.json()
        item.job = reserved.json()
    item.identifier = UUID(item.job["id"])
    with item.case.database.session() as session: item.access = access_for(session, item.job["id"])
    item.cloud = Cloud(GcsConfiguration(enabled=True, bucket_name=item.settings.gcs_bucket_name,
        staging_prefix=item.settings.gcs_staging_prefix), item.case.database)
    item.coordinator = StagingCoordinator(item.case.database, item.worker, item.inventory, item.cloud, item.settings)
    item.case.app = create_app(item.settings, item.case.database, item.case.worker,
        inventory_client=item.inventory, technical_client=item.technical, packaging_client=item.worker, gcs_client=item.cloud)
    return item


def body(item, **changes):
    return Action(**(dict(idempotency_key=uuid4(), expected_plan_sha256=item.job["plan_sha256"],
        reason="Synthetic staging dispatch") | changes))


@pytest.mark.parametrize("enabled", [False, True])
def test_history_reports_deployed_transfer_configuration(runtime_case, enabled):
    item = runtime_case
    item.case.app = create_app(item.settings.model_copy(update={"gcs_enabled": enabled}), item.case.database,
        inventory_client=item.inventory, technical_client=item.technical, packaging_client=item.worker)
    with item.case.client("ADMIN") as client:
        response = client.get(PATH)
        assert response.status_code == 200 and response.json()["enabled"] is enabled
        assert response.json()["items"][0]["id"] == item.job["id"]


def perform(item, action="EXECUTE", payload=None):
    return asyncio.run(item.coordinator.perform(item.identifier, payload or body(item), item.access, action))


def test_execute_commits_every_intent_streams_all_sources_and_accepts_exact_marker(runtime_case):
    item = runtime_case; payload = body(item)
    result = perform(item, payload=payload)
    assert result["status"] == "STAGED_VERIFIED", result
    assert [method for method, _ in item.cloud.calls] == ["upload"] * (item.job["object_count"] + 1)
    assert item.cloud.calls[-1][1].relative_path == "_reawote/complete.json"
    assert item.opened == item.closed == len(item.bytes)
    count = len(item.cloud.calls)
    assert perform(item, payload=payload) == result and len(item.cloud.calls) == count
    manifest = json.loads(item.cloud.objects["_reawote/complete.json"])
    assert manifest and len(item.cloud.objects) == item.job["object_count"] + 1
    with item.case.database.session() as session:
        assert len(list(session.scalars(select(PublicationStagingDispatch)))) == 1
        assert len(list(session.scalars(select(PublicationStagingObservation)))) == count
        observed = session.scalar(select(PublicationStagingResult))
        assert observed.outcome == "VERIFIED" and observed.inputs_current and observed.actor_current and observed.lease_current


def test_reconciliation_performs_only_readback_and_replays_no_upload(runtime_case):
    item = runtime_case
    first = perform(item)
    count = len(item.cloud.calls); opened = item.opened
    second = perform(item, "RECONCILE", body(item, expected_last_dispatch_id=UUID(first["last_dispatch_id"])))
    assert second["status"] == "STAGED_VERIFIED", second
    assert all(method == "reconcile" for method, _ in item.cloud.calls[count:])
    assert item.opened == opened


@pytest.mark.parametrize("role,expected", [(None,401), ("PROCESSOR",403), ("PRODUCTION_LEAD",403), ("LEADERSHIP",200)])
def test_run_endpoint_enforces_real_session_and_publication_roles(runtime_case, role, expected):
    item = runtime_case
    with item.case.client(role) as client:
        response = client.post(PATH + "/" + item.job["id"] + "/run", json=body(item).model_dump(mode="json"))
        assert response.status_code == expected, response.json()
        if expected == 200:
            assert response.json()["status"] == "STAGED_VERIFIED"
            assert client.get(item.material_path).json()["is_published"] is False
        else: assert not item.cloud.calls


@pytest.mark.parametrize("change", ["csrf", "plan", "progress", "disabled", "destination"])
def test_pre_dispatch_failure_records_no_intent_and_opens_no_source(runtime_case, change):
    item = runtime_case; payload = body(item).model_dump(mode="json")
    if change == "plan": payload["expected_plan_sha256"] = "a" * 64
    if change == "progress": payload["expected_last_dispatch_id"] = str(uuid4())
    if change == "disabled":
        settings = item.settings.model_copy(update={"gcs_enabled": False})
        item.case.app = create_app(settings, item.case.database, item.case.worker,
            inventory_client=item.inventory, technical_client=item.technical, packaging_client=item.worker, gcs_client=item.cloud)
    if change == "destination": item.cloud.configuration = item.cloud.configuration.model_copy(update={"bucket_name": "wrong-bucket"})
    with item.case.client("ADMIN") as client:
        if change == "csrf": del client.headers["X-CSRF-Token"]
        response = client.post(PATH + "/" + item.job["id"] + "/run", json=payload)
        assert response.status_code == {"csrf":403, "disabled":503, "destination":503}.get(change,409), response.json()
    with item.case.database.session() as session:
        assert session.scalar(select(PublicationStagingDispatch)) is None
        assert session.get(PublicationStagingState, item.identifier).status == "RESERVED"
    assert not item.cloud.calls and item.opened == 0


@pytest.mark.parametrize("change", ["demote", "disable", "password", "revoke", "material"])
def test_revocation_or_input_change_during_upload_never_accepts_marker(runtime_case, change):
    from datetime import datetime, UTC
    item = runtime_case
    async def mutate(spec):
        with item.case.database.session() as session:
            job = session.get(PublicationStagingJob, item.identifier)
            if change == "demote": session.get(InternalUser, job.actor_id).role = "PROCESSOR"
            elif change == "disable": session.get(InternalUser, job.actor_id).is_active = False
            elif change == "password": session.get(UserCredential, job.actor_id).must_change_password = True
            elif change == "revoke": session.get(AuthSession, job.issuer_session_id).revoked_at = datetime.now(UTC)
            else: session.get(PBRMaterial, item.material.id).material_name = "Out-of-band edit during upload"
            session.commit()
    item.cloud.after = mutate
    with pytest.raises(HTTPException) as failure: perform(item)
    assert failure.value.status_code == {"demote":403, "disable":401, "password":403, "revoke":401, "material":409}[change]
    assert "_reawote/complete.json" not in item.cloud.objects
    with item.case.database.session() as session:
        assert session.get(PublicationStagingState, item.identifier).status == "RECOVERY_REQUIRED"
        result = session.scalar(select(PublicationStagingResult))
        assert result.outcome == "UNCERTAIN" and not result.inputs_current
    assert item.opened == item.closed == 1


@pytest.mark.parametrize("when", ["before-dispatch", "before-marker", "after-marker"])
def test_live_source_changes_prevent_acceptance_even_when_all_receipts_exist(runtime_case, when):
    item = runtime_case
    if when == "before-dispatch": item.inventory.changed = True
    else:
        async def changed(spec):
            if when == "before-marker" or spec.relative_path == "_reawote/complete.json": item.inventory.changed = True
        item.cloud.after = changed
    if when == "before-dispatch":
        with pytest.raises(HTTPException) as failure: perform(item)
        assert failure.value.detail["code"] == "GCS_SOURCE_CHANGED" and not item.cloud.calls
    else:
        response = perform(item)
        assert response["status"] == "RECOVERY_REQUIRED"
        with item.case.database.session() as session:
            result = session.scalar(select(PublicationStagingResult))
            assert result.outcome == ("VERIFIED" if when == "after-marker" else "UNCERTAIN") and not result.inputs_current
        assert ("_reawote/complete.json" in item.cloud.objects) is (when == "after-marker")


@pytest.mark.parametrize("corrupt", ["bucket", "size", "binding"])
def test_forged_transport_receipt_is_rejected_before_next_object(runtime_case, corrupt):
    item = runtime_case
    def forge(receipt):
        if corrupt == "bucket": return receipt.model_copy(update={"bucket_name": "wrong-bucket"})
        spec = receipt.spec.model_copy(update={"size": True} if corrupt == "size" else {"binding_sha256": "a" * 64})
        return receipt.model_copy(update={"spec": spec})
    item.cloud.corrupt = forge
    assert perform(item)["status"] == "RECOVERY_REQUIRED"
    assert len(item.cloud.calls) == 1
    with item.case.database.session() as session:
        observed = session.scalar(select(PublicationStagingObservation))
        assert observed.outcome == "UNCERTAIN" and observed.failure_code == "GCS_VERIFICATION_FAILED" and observed.receipt is None


def test_partial_upload_can_only_reconcile_and_requires_fresh_namespace_to_write_again(runtime_case):
    item = runtime_case
    item.cloud.failure = GcsError("GCS_OUTCOME_UNCERTAIN")
    first = perform(item)
    assert first["status"] == "RECOVERY_REQUIRED"
    with pytest.raises(HTTPException) as error:
        perform(item, payload=body(item, expected_last_dispatch_id=UUID(first["last_dispatch_id"])))
    assert error.value.detail["code"] == "GCS_STAGING_RECONCILIATION_REQUIRED"
    item.cloud.failure = None
    second = perform(item, "RECONCILE", body(item, expected_last_dispatch_id=UUID(first["last_dispatch_id"])))
    assert second["status"] == "RECOVERY_REQUIRED"
    assert [method for method, _ in item.cloud.calls] == ["upload", "reconcile"] and not item.cloud.objects


@pytest.mark.parametrize("interruption", ["cancel", "deadline"])
def test_cancelled_or_timed_out_transfer_records_uncertainty_and_releases_resources(runtime_case, monkeypatch, interruption):
    from app import staging_runtime
    item = runtime_case; entered = None
    async def block(spec):
        entered.set(); await anyio.sleep_forever()
    async def run():
        nonlocal entered
        entered = anyio.Event(); item.cloud.before = block
        if interruption == "deadline":
            monkeypatch.setattr(staging_runtime, "MAX_JOB_SECONDS", 2)
            return await item.coordinator.perform(item.identifier, body(item), item.access, "EXECUTE")
        async with anyio.create_task_group() as group:
            group.start_soon(item.coordinator.perform, item.identifier, body(item), item.access, "EXECUTE")
            await entered.wait(); group.cancel_scope.cancel()
    response = anyio.run(run)
    if response is not None: assert response["status"] == "RECOVERY_REQUIRED"
    with item.case.database.session() as session:
        state = session.get(PublicationStagingState, item.identifier)
        assert state.status == "RECOVERY_REQUIRED"
        expected = state.last_dispatch_id
        observed = session.scalar(select(PublicationStagingObservation))
        assert observed.outcome == "UNCERTAIN"
    assert item.opened == item.closed == 1
    item.cloud.before = None
    assert perform(item, "RECONCILE", body(item, expected_last_dispatch_id=expected))["status"] == "RECOVERY_REQUIRED"
