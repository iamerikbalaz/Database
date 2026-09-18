"""Current operator access to immutable accepted packaging artifacts."""
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from app.api.material_approvals import PUBLICATION_APPROVERS
from app.api.material_review import _material
from app.auth.access import AccessDependency
from app.db.models import MaterialPackagingState, MaterialPackagingObservation, MaterialPackagingDispatch
from app.material_review import canonical_hash
from app.packaging_client import PackagingClientError
from app.packaging_contract import PreparedPackaging, PackagingDispatch, DispatchedPackagingResult
from app.packaging_jobs import execution, frozen_report, validate_dispatched
from app.schemas import Sha256

CHUNK_BYTES = 64 * 1024
RECHECK_BYTES = 8 * 1024**2
RECHECK_SECONDS = 1
PRIVATE_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def file_id(path): return hashlib.sha256(path.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DownloadContext:
    execution_id: UUID
    observation_id: UUID
    prepared: PreparedPackaging
    report: dict
    result: DispatchedPackagingResult


def accepted(session, material_id, execution_id, access):
    access.check(session, PUBLICATION_APPROVERS)
    _material(session, material_id, access, historical=True)
    item = execution(session, material_id, execution_id)
    state = session.get(MaterialPackagingState, item.id)
    if state is None or state.status != "PACKAGED": raise HTTPException(409, {"code": "PACKAGING_DOWNLOAD_NOT_ACCEPTED"})
    observed = session.get(MaterialPackagingObservation, state.last_observation_id)
    command = session.get(MaterialPackagingDispatch, state.last_dispatch_id)
    try:
        if (observed is None or command is None or observed.execution_id != item.id or command.execution_id != item.id
                or observed.dispatch_id != command.id or observed.outcome != "READY" or not observed.inputs_current
                or not observed.actor_current or canonical_hash(item.input_snapshot) != item.input_hash): raise ValueError()
        prepared = PreparedPackaging.model_validate_json(json.dumps(item.worker_request))
        if prepared.request_hash != item.worker_request_hash or prepared.request.operation_id != str(item.id): raise ValueError()
        report = frozen_report(session, item.input_snapshot)
        result = validate_dispatched(DispatchedPackagingResult.model_validate_json(json.dumps(observed.worker_result)),
            prepared, report, PackagingDispatch(id=str(command.id), ordinal=command.ordinal, action=command.action))
        if result.terminal != "OPEN" or result.stored.proof_sha256 != observed.proof_sha256: raise ValueError()
        return DownloadContext(item.id, observed.id, prepared, report, result)
    except (PackagingClientError, ValueError, TypeError, KeyError, AttributeError, ArithmeticError, RecursionError):
        raise HTTPException(503, {"code": "PACKAGING_DOWNLOAD_UNAVAILABLE"}) from None


class AuthorizedArtifactResponse(Response):
    def __init__(self, *, database, worker, access, material_id, context, item):
        super().__init__(b"", media_type="application/octet-stream")
        self.database = database; self.worker = worker; self.access = access
        self.material_id = material_id; self.context = context; self.item = item

    def authorize(self):
        with self.database.session() as session:
            self.access.check(session, PUBLICATION_APPROVERS)
            _material(session, self.material_id, self.access, historical=True)
            execution(session, self.material_id, self.context.execution_id)
            state = session.get(MaterialPackagingState, self.context.execution_id)
            if state is None or state.status != "PACKAGED" or state.last_observation_id != self.context.observation_id:
                raise HTTPException(409, {"code": "PACKAGING_DOWNLOAD_NOT_ACCEPTED"})

    async def __call__(self, scope, receive, send):
        started = False
        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start": started = True
            await send(message)

        async def chunks(upstream):
            total = 0; checked = 0; stamp = time.monotonic(); pending = b""; digest = hashlib.sha256()
            async for block in upstream.chunks():
                if not isinstance(block, bytes) or len(block) > CHUNK_BYTES: raise PackagingClientError("PACKAGING_DOWNLOAD_UNAVAILABLE")
                total += len(block); digest.update(block)
                if total > self.item.size: raise PackagingClientError("PACKAGING_DOWNLOAD_UNAVAILABLE")
                if total - checked >= RECHECK_BYTES or time.monotonic() - stamp >= RECHECK_SECONDS:
                    await run_in_threadpool(self.authorize); checked = total; stamp = time.monotonic()
                if pending: yield pending
                pending = block
            if total != self.item.size or digest.hexdigest() != self.item.sha256: raise PackagingClientError("PACKAGING_DOWNLOAD_UNAVAILABLE")
            await run_in_threadpool(self.authorize)
            if pending: yield pending

        try:
            await run_in_threadpool(self.authorize)
            context = self.context
            async with self.worker.open_artifact(context.prepared, context.report, context.result, self.item.path) as upstream:
                # Recheck the account after the worker has verified/opened bytes,
                # before disclosing headers or data. No transaction spans that IO.
                await run_in_threadpool(self.authorize)
                if upstream.file != self.item or upstream.proof_sha256 != context.result.stored.proof_sha256:
                    raise PackagingClientError("PACKAGING_DOWNLOAD_UNAVAILABLE")
                headers = {**PRIVATE_HEADERS, "Content-Length": str(self.item.size),
                    "Content-Disposition": 'attachment; filename="package.bin"; filename*=UTF-8\'\'' + quote(self.item.path.rsplit("/", 1)[-1], safe=""),
                    "X-Packaging-Proof-Sha256": context.result.stored.proof_sha256, "X-Packaging-File-Sha256": self.item.sha256}
                await StreamingResponse(chunks(upstream), media_type="application/octet-stream", headers=headers)(scope, receive, tracked_send)
        except ClientDisconnect: return
        except (HTTPException, PackagingClientError) as error:
            if started: raise RuntimeError("Packaging transfer interrupted.") from None
            # A failed worker call also renews account access before exposing its
            # fixed error code. Demoted/disabled callers receive only auth failure.
            if isinstance(error, PackagingClientError):
                try: await run_in_threadpool(self.authorize)
                except HTTPException as auth_error: error = auth_error
            status = error.status_code if isinstance(error, HTTPException) else 503
            detail = error.detail if isinstance(error, HTTPException) else {"code": error.code}
            await JSONResponse({"detail": detail}, status_code=status, headers=PRIVATE_HEADERS)(scope, receive, tracked_send)


def build_packaging_downloads_router(database, worker):
    router = APIRouter()

    @router.get("/{execution_id}/artifacts")
    def listing(material_id: UUID, execution_id: UUID, access: AccessDependency,
        after: Annotated[int, Query(ge=0, le=20008)] = 0, limit: Annotated[int, Query(ge=1, le=50)] = 20):
        with database.session() as session:
            context = accepted(session, material_id, execution_id, access)
            files = context.result.stored.payload.files
            items = [{"id": file_id(item.path), **item.model_dump(mode="json")} for item in files[after:after + limit]]
            return JSONResponse({"execution_id": str(execution_id), "proof_sha256": context.result.stored.proof_sha256,
                "items": items, "next_cursor": after + limit if after + limit < len(files) else None}, headers=PRIVATE_HEADERS)

    @router.get("/{execution_id}/artifacts/{artifact_id}")
    def download(material_id: UUID, execution_id: UUID, artifact_id: Sha256, proof_sha256: Sha256,
        access: AccessDependency, request: Request):
        with database.session() as session:
            context = accepted(session, material_id, execution_id, access)
            if proof_sha256 != context.result.stored.proof_sha256: raise HTTPException(409, {"code": "PACKAGING_DOWNLOAD_PROOF_CHANGED"})
            item = next((item for item in context.result.stored.payload.files if file_id(item.path) == artifact_id), None)
            if item is None: raise HTTPException(404, {"code": "PACKAGING_DOWNLOAD_FILE_NOT_FOUND"})
            if "range" in request.headers: raise HTTPException(422, {"code": "PACKAGING_DOWNLOAD_RANGE_UNSUPPORTED"})
        return AuthorizedArtifactResponse(database=database, worker=worker, access=access, material_id=material_id, context=context, item=item)

    return router
