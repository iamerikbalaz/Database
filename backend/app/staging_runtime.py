"""Explicit create-only upload or read-only reconciliation of a reserved batch.

Database transactions end before external IO. Every object intent is committed
before its source opens. A process crash leaves durable RUNNING progress for an
explicit reconciliation or administrative abandonment.
"""
from contextlib import contextmanager
from functools import partial
from threading import BoundedSemaphore

import anyio
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.api.material_approvals import PUBLICATION_APPROVERS
from app.api.publication_staging_jobs import (_commit, _conflict, _digest, _job,
    _request_gate, _require_database, _view)
from app.db.models import (MaterialAuditEvent, MaterialPackagingExecution, PublicationStagingItem,
    PublicationStagingJob, PublicationStagingState, PublicationStagingDispatch, PublicationStagingTransfer,
    PublicationStagingObservation, PublicationStagingResult, PublicationStagingOwner, PBRMaterial)
from app.gcs_batch import completion_manifest
from app.gcs_contract import GcsError, GcsConfiguration, GcsObjectReceipt
from app.inventory_client import SourceInventory
from app.publication_staging import reserved_staging
from app.staging_dispatch_lease import StagingLeaseError, staging_dispatch_lease
from app.staging_sources import StagingSources

MAX_JOB_SECONDS = 3600


@contextmanager
def _session(database):
    with database.session() as session:
        if session.get_bind().dialect.name == "postgresql":
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            session.execute(text("SET LOCAL lock_timeout = '5000ms'"))
        yield session


def verified_receipt(value, spec, plan):
    try:
        receipt = GcsObjectReceipt.model_validate(value.model_dump(mode="python", warnings="error"))
        if (receipt.spec != spec or receipt.bucket_name != plan.body.bucket_name
                or receipt.object_name != f"{plan.body.staging_prefix}/{plan.body.job_id}/{spec.relative_path}"):
            raise ValueError()
        return receipt
    except Exception:
        raise GcsError("GCS_VERIFICATION_FAILED") from None


def fresh_sources(inventory, prepared):
    """Read only; a cancelled waiter never leaves a background database writer."""
    try:
        for package in prepared.packages:
            request = package.prepared.request
            observed = SourceInventory.model_validate_json(inventory.inventory("/".join(request.parts)).model_dump_json())
            if observed.folder_name != request.parts[-1] or observed.source_revision_hash != request.source_revision_hash:
                raise GcsError("GCS_SOURCE_CHANGED")
    except GcsError: raise
    except Exception:
        raise GcsError("GCS_SOURCE_UNAVAILABLE") from None


class StagingCoordinator:
    def __init__(self, database, worker, inventory, cloud, settings):
        self.database = database; self.worker = worker; self.inventory = inventory
        self.cloud = cloud; self.settings = settings; self._slot = BoundedSemaphore(1)

    async def perform(self, job_id, payload, access, action):
        if action not in {"EXECUTE", "RECONCILE"}: raise ValueError("Invalid staging action")
        run = _Run(self, job_id, payload, access, action)
        try: replay = await run_in_threadpool(run.precheck)
        except GcsError as error: raise HTTPException(503, {"code": error.code}) from None
        except SQLAlchemyError: raise HTTPException(503, {"code": "GCS_STAGING_HISTORY_UNAVAILABLE"}) from None
        if replay is not None: return replay
        if not self._slot.acquire(blocking=False): raise HTTPException(409, {"code": "GCS_BUSY"})
        lifetime = staging_dispatch_lease(self.database.engine, job_id,
            allow_test_sqlite=self.settings.app_env == "test")
        entered = False
        try:
            run.lease = await run_in_threadpool(lifetime.__enter__); entered = True
            try:
                with anyio.fail_after(MAX_JOB_SECONDS):
                    prepared = await run_in_threadpool(run.prepare)
                    if isinstance(prepared, dict): return prepared  # Exact replay after lease acquisition.
                    run.prepared = prepared
                    if action == "EXECUTE": await run.sources_current()
                    replay = await run_in_threadpool(run.start)
                    if replay is not None: return replay
                    await run.objects()
                    await run.sources_current()
                    return await run_in_threadpool(run.finish, True, None)
            except BaseException as error:
                failure = error.code if isinstance(error, GcsError) else "GCS_OUTCOME_UNCERTAIN"
                if isinstance(error, (HTTPException, StagingLeaseError)): failure = "GCS_OPERATION_BLOCKED"
                # Shield the short database record, never another external request.
                if run.dispatch_id is not None:
                    with anyio.CancelScope(shield=True):
                        try: response = await run_in_threadpool(run.finish, False, failure)
                        except HTTPException: raise
                        except Exception:
                            raise HTTPException(503, {"code": "GCS_STAGING_HISTORY_UNAVAILABLE"}) from None
                    if not isinstance(error, Exception): raise
                    if isinstance(error, HTTPException): raise
                    if isinstance(error, (GcsError, TimeoutError, StagingLeaseError)): return response
                if isinstance(error, HTTPException): raise
                if isinstance(error, GcsError): raise HTTPException(503, {"code": error.code}) from None
                if isinstance(error, StagingLeaseError): raise
                if not isinstance(error, Exception): raise
                raise HTTPException(503, {"code": "GCS_STAGING_UNAVAILABLE"}) from None
        except StagingLeaseError as error:
            raise HTTPException(409 if error.code == "GCS_DISPATCH_BUSY" else 503, {"code": error.code}) from None
        finally:
            try:
                if entered:
                    with anyio.CancelScope(shield=True):
                        await run_in_threadpool(lifetime.__exit__, None, None, None)
            finally: self._slot.release()


class _Run:
    def __init__(self, coordinator, job_id, payload, access, action):
        self.service = coordinator; self.job_id = job_id; self.payload = payload
        self.access = access; self.action = action
        self.digest = _digest("GCS_STAGING_" + action, payload, job_id)
        self.dispatch_id = None; self.intent_id = None; self.marker_id = None
        self.prepared = None; self.lease = None

    def replay(self, session, actor):
        previous = session.scalar(select(PublicationStagingDispatch).where(
            PublicationStagingDispatch.actor_id == actor.id,
            PublicationStagingDispatch.request_key == self.payload.idempotency_key))
        if previous and (previous.job_id != self.job_id or previous.request_hash != self.digest):
            _conflict("GCS_STAGING_REQUEST_KEY_REUSED")
        return previous

    def enabled(self):
        if not self.service.settings.gcs_enabled: raise GcsError("GCS_DISABLED")
        try:
            configured = GcsConfiguration.model_validate(self.service.cloud.configuration.model_dump(mode="python", warnings="error"))
            if (not configured.enabled or configured.bucket_name != self.service.settings.gcs_bucket_name
                    or configured.staging_prefix != self.service.settings.gcs_staging_prefix): raise ValueError()
            if self.prepared is not None and (configured.bucket_name != self.prepared.plan.body.bucket_name
                    or configured.staging_prefix != self.prepared.plan.body.staging_prefix): raise ValueError()
        except Exception: raise GcsError("GCS_CONFIGURATION_INVALID") from None

    def precheck(self):
        with _session(self.service.database) as session:
            actor = self.access.check(session, PUBLICATION_APPROVERS)
            _require_database(session, self.service.settings)
            job = _job(session, self.job_id)
            if self.replay(session, actor): return _view(session, job)
            self.progress(session)
        self.enabled()

    def progress(self, session):
        job = _job(session, self.job_id); state = session.get(PublicationStagingState, self.job_id)
        if state is None: _conflict("GCS_STAGING_HISTORY_INVALID")
        if job.plan_sha256 != self.payload.expected_plan_sha256: _conflict("GCS_STAGING_PLAN_CHANGED")
        if state.status == "CLOSED": _conflict("GCS_STAGING_ALREADY_CLOSED")
        if state.last_dispatch_id != self.payload.expected_last_dispatch_id: _conflict("GCS_STAGING_PROGRESS_CHANGED")
        if self.action == "EXECUTE" and (state.status != "RESERVED" or state.last_dispatch_id is not None):
            _conflict("GCS_STAGING_RECONCILIATION_REQUIRED")
        if self.action == "RECONCILE" and state.last_dispatch_id is None: _conflict("GCS_STAGING_NOT_DISPATCHED")
        return state

    def prepare(self):
        with _session(self.service.database) as session:
            actor = self.access.check(session, PUBLICATION_APPROVERS)
            if self.replay(session, actor): return _view(session, _job(session, self.job_id))
            self.progress(session); self.enabled(); self.lease.require_owned()
            return reserved_staging(session, self.job_id, self.access, self.service.settings)

    def locked_items(self, session):
        items = list(session.scalars(select(PublicationStagingItem).where(PublicationStagingItem.job_id == self.job_id)
            .order_by(PublicationStagingItem.material_id)))
        for item in items:
            # Persistence of late factual results is allowed after account revocation.
            session.scalar(select(PBRMaterial.id).where(PBRMaterial.id == item.material_id).with_for_update())
        session.scalar(select(PublicationStagingJob.id).where(PublicationStagingJob.id == self.job_id).with_for_update())
        return items

    def audit(self, session, items, actor_id, event, details):
        for item in items:
            package = session.get(MaterialPackagingExecution, item.execution_id)
            session.add(MaterialAuditEvent(material_id=item.material_id, actor_id=actor_id, event_type=event,
                generation=package.input_snapshot["generation"], revision_hash=package.input_snapshot["revision_hash"],
                result={"audit": {"job_id": str(self.job_id), "dispatch_id": str(self.dispatch_id), **details}}))

    def start(self):
        with _session(self.service.database) as session:
            actor = self.access.check(session, PUBLICATION_APPROVERS)
            _request_gate(session, actor.id, self.payload.idempotency_key, "dispatch")
            if self.replay(session, actor): return _view(session, _job(session, self.job_id))
            current = reserved_staging(session, self.job_id, self.access, self.service.settings)
            if current != self.prepared: _conflict("GCS_STAGING_INPUTS_CHANGED")
            items = self.locked_items(session); state = self.progress(session)
            previous = session.get(PublicationStagingDispatch, state.last_dispatch_id) if state.last_dispatch_id else None
            ordinal = previous.ordinal + 1 if previous else 1
            if ordinal > 2147483647: _conflict("GCS_STAGING_DISPATCH_LIMIT")
            command = PublicationStagingDispatch(job_id=self.job_id, ordinal=ordinal,
                previous_dispatch_id=state.last_dispatch_id, action=self.action, actor_id=actor.id,
                issuer_session_id=self.access.context.session.id, request_key=self.payload.idempotency_key,
                request_hash=self.digest, plan_sha256=current.plan.sha256, reason=self.payload.reason)
            session.add(command); session.flush(); self.dispatch_id = command.id
            state.status = "RUNNING"; state.last_dispatch_id = command.id; state.last_result_id = None
            self.audit(session, items, actor.id, "PUBLICATION_STAGING_DISPATCHED", dict(
                action=self.action, ordinal=ordinal, reason=self.payload.reason, plan_sha256=current.plan.sha256))
            self.enabled(); self.lease.require_owned(); _commit(session)

    def guard(self, *, full=False):
        self.enabled(); self.lease.require_owned()
        with _session(self.service.database) as session:
            self.access.check(session, PUBLICATION_APPROVERS)
            if full:
                current = reserved_staging(session, self.job_id, self.access, self.service.settings)
                if current != self.prepared: _conflict("GCS_STAGING_INPUTS_CHANGED")
            state = session.get(PublicationStagingState, self.job_id)
            owners = list(session.scalars(select(PublicationStagingOwner).where(PublicationStagingOwner.job_id == self.job_id)))
            if (state is None or state.status != "RUNNING" or state.last_dispatch_id != self.dispatch_id
                    or len(owners) != len(self.prepared.packages) or not all(owner.active for owner in owners)):
                _conflict("GCS_STAGING_OWNERSHIP_CHANGED")
        self.lease.require_owned()

    async def operation_guard(self):
        await run_in_threadpool(self.guard)

    async def sources_current(self):
        # Only this read-only operation may outlive a cancelled wait. It holds no
        # database transaction, dispatch connection or cloud write capability.
        await anyio.to_thread.run_sync(partial(fresh_sources, self.service.inventory, self.prepared), abandon_on_cancel=True)

    def intent(self, spec, ordinal, kind):
        with _session(self.service.database) as session:
            self.access.check(session, PUBLICATION_APPROVERS)
            current = reserved_staging(session, self.job_id, self.access, self.service.settings)
            if current != self.prepared: _conflict("GCS_STAGING_INPUTS_CHANGED")
            self.locked_items(session)
            state = session.get(PublicationStagingState, self.job_id)
            if state.status != "RUNNING" or state.last_dispatch_id != self.dispatch_id:
                _conflict("GCS_STAGING_PROGRESS_CHANGED")
            intent = PublicationStagingTransfer(job_id=self.job_id, dispatch_id=self.dispatch_id,
                ordinal=ordinal, kind=kind, relative_path=spec.relative_path, size=spec.size, sha256=spec.sha256)
            session.add(intent); session.flush(); self.intent_id = intent.id
            self.enabled(); self.lease.require_owned(); _commit(session)
            return intent.id

    def observed(self, intent_id, receipt=None, failure=None):
        with _session(self.service.database) as session:
            previous = session.scalar(select(PublicationStagingObservation).where(PublicationStagingObservation.transfer_id == intent_id))
            if previous: return previous.id
            observed = PublicationStagingObservation(job_id=self.job_id, dispatch_id=self.dispatch_id,
                transfer_id=intent_id, outcome="VERIFIED" if receipt is not None else "UNCERTAIN",
                receipt=receipt.model_dump(mode="json") if receipt is not None else None,
                failure_code=None if receipt is not None else GcsError(failure).code)
            session.add(observed); session.flush(); _commit(session); return observed.id

    async def transfer(self, spec, ordinal, kind, sources=None, data=None):
        intent_id = await run_in_threadpool(self.intent, spec, ordinal, kind)
        try:
            if self.action == "RECONCILE":
                value = await self.service.cloud.reconcile(spec, operation_guard=self.operation_guard)
            elif sources is not None:
                async with sources.open(spec) as chunks:
                    value = await self.service.cloud.upload(spec, chunks, operation_guard=self.operation_guard)
            else:
                async def chunks():
                    for start in range(0, len(data), 64 * 1024): yield data[start:start + 64 * 1024]
                value = await self.service.cloud.upload(spec, chunks(), operation_guard=self.operation_guard)
            receipt = verified_receipt(value, spec, self.prepared.plan)
            observed_id = await run_in_threadpool(self.observed, intent_id, receipt)
            return receipt, observed_id
        except BaseException as error:
            failure = error.code if isinstance(error, GcsError) else "GCS_OUTCOME_UNCERTAIN"
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(self.observed, intent_id, None, failure)
            raise

    async def objects(self):
        sources = StagingSources(self.prepared, self.service.worker, operation_guard=self.operation_guard)
        receipts = []
        for ordinal, spec in enumerate(self.prepared.plan.specifications(), start=1):
            receipt, _ = await self.transfer(spec, ordinal, "DATA", sources=sources)
            receipts.append(receipt)
        manifest = completion_manifest(self.prepared.plan, tuple(receipts))
        if self.action == "EXECUTE": await self.sources_current()
        await run_in_threadpool(partial(self.guard, full=True))
        _, self.marker_id = await self.transfer(manifest.specification, len(receipts) + 1, "MARKER", data=manifest.data)

    def finish(self, source_current, failure):
        auth_error = None; acceptance_failure = failure
        with _session(self.service.database) as session:
            try: self.access.check(session, PUBLICATION_APPROVERS)
            except HTTPException as error: auth_error = error
            items = self.locked_items(session)
            command = session.get(PublicationStagingDispatch, self.dispatch_id)
            if command is None: raise HTTPException(503, {"code": "GCS_STAGING_HISTORY_UNAVAILABLE"})
            state = session.get(PublicationStagingState, self.job_id)
            prior = session.scalar(select(PublicationStagingResult).where(PublicationStagingResult.dispatch_id == self.dispatch_id))
            if prior is None:
                inputs_current = False; lease_current = True
                if auth_error is None and source_current and state.status != "CLOSED" and state.last_dispatch_id == self.dispatch_id:
                    try:
                        inputs_current = reserved_staging(session, self.job_id, self.access, self.service.settings) == self.prepared
                    except HTTPException: acceptance_failure = "GCS_OPERATION_BLOCKED"
                try: self.enabled(); self.lease.require_owned()
                except (GcsError, StagingLeaseError):
                    lease_current = False; acceptance_failure = "GCS_OPERATION_BLOCKED"
                result = PublicationStagingResult(job_id=self.job_id, dispatch_id=self.dispatch_id,
                    outcome="VERIFIED" if self.marker_id is not None else "UNCERTAIN",
                    completion_observation_id=self.marker_id,
                    failure_code=None if self.marker_id is not None else GcsError(failure).code,
                    inputs_current=inputs_current, actor_current=auth_error is None, lease_current=lease_current)
                session.add(result); session.flush()
                if state.status != "CLOSED" and state.last_dispatch_id == self.dispatch_id:
                    state.status = ("STAGED_VERIFIED" if self.marker_id is not None and inputs_current
                        and auth_error is None and lease_current else "RECOVERY_REQUIRED")
                    state.last_result_id = result.id
                self.audit(session, items, command.actor_id, "PUBLICATION_STAGING_OBSERVED", dict(
                    result_id=str(result.id), outcome=result.outcome, inputs_current=inputs_current,
                    actor_current=auth_error is None, lease_current=lease_current, failure_code=result.failure_code,
                    acceptance_failure_code=GcsError(acceptance_failure).code if acceptance_failure else None))
                _commit(session)
            if auth_error: raise auth_error
            return _view(session, _job(session, self.job_id))
