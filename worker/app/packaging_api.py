"""Opt-in private packaging service. It never grants application authorization."""
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
import hmac
import json
import os
from pathlib import Path
import re
from threading import BoundedSemaphore
from typing import Annotated, Literal
from uuid import UUID

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from app.api import TechnicalValidationResponse
from app.packaging_convert import PackagingConversionError, verify_runtime
from app.packaging_execution import (ExecutionLimits, ExecutionRoots, PackagingRequest, PackagingExecutionError,
    _roots, _validate_request, execute_packaging, prepare_packaging_request, reconcile_packaging)
from app.packaging_stage import PackagingStageError

MAX_BODY_BYTES = 8 * 1024**2


@dataclass(frozen=True)
class PackagingServiceSettings:
    enabled: bool = False
    token: str | None = field(default=None, repr=False)
    roots: ExecutionRoots | None = None

    @classmethod
    def from_environment(cls):
        names = ("MATERIALS_ROOT", "PACKAGING_WORKSPACE_ROOT", "PACKAGING_ARTIFACT_ROOT", "PACKAGING_JOURNAL_ROOT")
        paths = [os.getenv(name) for name in names]
        roots = ExecutionRoots(*(Path(value) for value in paths)) if all(paths) else None
        return cls(os.getenv("PACKAGING_ENABLED", "false").lower() == "true", os.getenv("PACKAGING_SERVICE_TOKEN"), roots)

    def configured(self):
        return (self.enabled is True and isinstance(self.roots, ExecutionRoots) and isinstance(self.token, str)
            and 32 <= len(self.token) <= 256 and self.token.isascii() and all(33 <= ord(char) <= 126 for char in self.token))


class _Boundary:
    def __init__(self, app, *, settings, state):
        self.app = app; self.settings = settings; self.state = state

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http": return await self.app(scope, receive, send)
        async def private_send(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message["headers"], (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")]}
            await send(message)
        async def reject(code, status):
            await JSONResponse({"detail": {"code": code}}, status_code=status)(scope, receive, private_send)
        if scope["path"] == "/health" and scope["method"] == "GET":
            return await self.app(scope, receive, private_send)
        if not self.settings.configured(): return await reject("PACKAGING_SERVICE_DISABLED", 503)
        headers = scope.get("headers", [])
        credentials = [value for key, value in headers if key.lower() == b"authorization"]
        if len(credentials) != 1 or not hmac.compare_digest(credentials[0], b"Bearer " + self.settings.token.encode("ascii")):
            return await reject("PACKAGING_SERVICE_UNAUTHORIZED", 401)
        if not self.state.available: return await reject("PACKAGING_SERVICE_UNAVAILABLE", 503)
        if scope["method"] != "POST": return await reject("PACKAGING_SERVICE_NOT_FOUND", 404)
        lengths = [value for key, value in headers if key.lower() == b"content-length"]
        if len(lengths) > 1 or lengths and (not re.fullmatch(rb"[0-9]{1,10}", lengths[0]) or int(lengths[0]) > MAX_BODY_BYTES):
            return await reject("PACKAGING_REQUEST_TOO_LARGE", 413)
        types = [value.split(b";", 1)[0].strip().lower() for key, value in headers if key.lower() == b"content-type"]
        if types != [b"application/json"]: return await reject("PACKAGING_REQUEST_INVALID", 422)
        size = 0; chunks = []
        try:
            with anyio.fail_after(15):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect": return
                    chunk = message.get("body", b""); size += len(chunk)
                    if size > MAX_BODY_BYTES: return await reject("PACKAGING_REQUEST_TOO_LARGE", 413)
                    chunks.append(chunk)
                    if not message.get("more_body", False): break
        except TimeoutError: return await reject("PACKAGING_REQUEST_TIMEOUT", 408)
        if lengths and size != int(lengths[0]): return await reject("PACKAGING_REQUEST_INVALID", 422)
        body = b"".join(chunks); sent = False
        async def replay():
            nonlocal sent
            if sent: return await receive()
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await self.app(scope, replay, private_send)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
OperationId = Annotated[str, Field(pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$")]
Parts = Annotated[list[Annotated[str, Field(min_length=1, max_length=255)]], Field(min_length=1, max_length=16)]
Policy = Literal["LEGACY_BEFORE_2026_03_04", "CURRENT_ON_OR_AFTER_2026_03_04"]


class Limits(StrictModel):
    seconds: Annotated[int, Field(ge=1, le=3600)] = 1800
    staged_bytes: Annotated[int, Field(ge=1, le=256 * 1024**3)] = 8 * 1024**3
    generated_bytes: Annotated[int, Field(ge=1, le=128 * 1024**3)] = 16 * 1024**3
    retained_bytes: Annotated[int, Field(ge=1, le=128 * 1024**3)] = 16 * 1024**3

    def value(self): return ExecutionLimits(**self.model_dump())


class PreparedRequest(StrictModel):
    version: Annotated[int, Field(ge=1, le=1)]
    operation_id: OperationId
    parts: Parts
    source_revision_hash: Sha
    technical_report_hash: Sha
    approval_context_hash: Sha
    policy: Policy
    storage_timezone: Annotated[str, Field(min_length=1, max_length=100)]
    plan_hash: Sha
    limits: Limits

    def value(self):
        result = PackagingRequest(UUID(self.operation_id), tuple(self.parts), self.source_revision_hash,
            self.technical_report_hash, self.approval_context_hash, self.policy, self.storage_timezone, self.plan_hash, self.limits.value())
        _validate_request(result)
        return result


class PrepareInput(StrictModel):
    operation_id: OperationId
    parts: Parts
    expected_source_revision_hash: Sha
    expected_technical_report_hash: Sha
    approval_context_hash: Sha
    policy: Policy
    storage_timezone: Annotated[str, Field(min_length=1, max_length=100)]
    report: dict
    limits: Limits = Field(default_factory=Limits)


class ReconcileInput(StrictModel):
    request: PreparedRequest
    request_hash: Sha

    def value(self):
        result = self.request.value()
        if result.sha256 != self.request_hash: raise PackagingExecutionError("PACKAGING_EXECUTION_REQUEST_CONFLICT")
        return result


class ExecuteInput(ReconcileInput):
    report: dict
    retry: bool = False


class DispatchCommand(StrictModel):
    id: OperationId
    ordinal: Annotated[int, Field(ge=1, le=2**31 - 1)]
    action: Literal["EXECUTE", "RETRY", "RECONCILE", "CLOSE"]

    def value(self):
        from app.packaging_dispatch import PackagingDispatch, validate_dispatch
        command = PackagingDispatch(UUID(self.id), self.ordinal, self.action)
        validate_dispatch(command)
        return command


class DispatchInput(ReconcileInput):
    dispatch: DispatchCommand
    report: dict | None = None


class ArtifactInput(StrictModel):
    operation_id: OperationId
    request_hash: Sha
    plan_hash: Sha
    proof_sha256: Sha
    path: Annotated[str, Field(min_length=1, max_length=4096)]
    size: Annotated[int, Field(ge=0, le=16 * 1024**3)]
    sha256: Sha

    @property
    def operation_uuid(self): return UUID(self.operation_id)


def _report(value):
    try: TechnicalValidationResponse.model_validate_json(json.dumps(value, allow_nan=False), strict=True)
    except (ValidationError, TypeError, ValueError): raise HTTPException(422, {"code": "PACKAGING_REQUEST_INVALID"}) from None
    return value


def _response(result):
    stored = result.stored
    return {"version": 1, "operation_id": str(result.operation_id), "request_hash": result.request_hash,
        "status": result.status, "attempt": result.attempt, "stored": None if stored is None else {
            "plan_hash": stored.plan_hash, "proof_sha256": stored.proof_sha256, "attempt": stored.attempt,
            "attempt_history": list(stored.attempt_history), "payload": stored.payload}}


def create_packaging_app(settings: PackagingServiceSettings | None = None):
    settings = settings if settings is not None else PackagingServiceSettings.from_environment()
    slot = BoundedSemaphore(1)
    @asynccontextmanager
    async def lifespan(application):
        application.state.available = False
        if settings.configured():
            def verify():
                verify_runtime()
                with _roots(settings.roots): pass
            try:
                await run_in_threadpool(verify)
                application.state.available = True
            except (PackagingExecutionError, PackagingStageError, PackagingConversionError, OSError, ValueError):
                pass
        yield
        application.state.available = False
    application = FastAPI(title="REAWOTE Private Packaging", version="1.0", lifespan=lifespan,
        docs_url=None, redoc_url=None, openapi_url=None)
    application.state.available = False
    application.state.execution_slot = slot
    application.add_middleware(_Boundary, settings=settings, state=application.state)

    @application.exception_handler(RequestValidationError)
    async def invalid(_: Request, __: RequestValidationError):
        return JSONResponse({"detail": {"code": "PACKAGING_REQUEST_INVALID"}}, status_code=422)

    @application.exception_handler(PackagingExecutionError)
    async def failure(_: Request, error: PackagingExecutionError):
        code = str(error)
        if not re.fullmatch(r"PACKAGING_[A-Z0-9_]{1,100}", code): code = "PACKAGING_EXECUTION_FAILED"
        status = 503 if code.endswith("_BUSY") else 404 if code == "PACKAGING_EXECUTION_NOT_FOUND" else 409
        return JSONResponse({"detail": {"code": code}}, status_code=status)

    @contextmanager
    def available_slot():
        if not slot.acquire(blocking=False): raise HTTPException(503, {"code": "PACKAGING_SERVICE_BUSY"}, headers={"Retry-After": "1"})
        try: yield
        finally: slot.release()

    @application.get("/health")
    def health():
        return JSONResponse({"service": "packaging", "status": "ready" if application.state.available else "disabled"},
            status_code=200 if application.state.available else 503)

    @application.post("/internal/packaging/prepare")
    def prepare(value: PrepareInput):
        with available_slot():
            request = prepare_packaging_request(_report(value.report), operation_id=UUID(value.operation_id), parts=tuple(value.parts),
                expected_source_revision_hash=value.expected_source_revision_hash, expected_technical_report_hash=value.expected_technical_report_hash,
                approval_context_hash=value.approval_context_hash, policy=value.policy, storage_timezone=value.storage_timezone, limits=value.limits.value())
            return {"request": request.document(), "request_hash": request.sha256}

    @application.post("/internal/packaging/execute")
    def execute(value: ExecuteInput):
        with available_slot():
            return _response(execute_packaging(value.value(), _report(value.report), roots=settings.roots, retry=value.retry))

    @application.post("/internal/packaging/dispatch")
    def dispatch(value: DispatchInput):
        from app.packaging_dispatch import dispatch_packaging
        with available_slot():
            result = dispatch_packaging(value.value(), value.dispatch.value(), roots=settings.roots,
                report=_report(value.report) if value.report is not None else None)
            return {**_response(result.result), "dispatch": result.dispatch.document(), "terminal": result.terminal}

    @application.post("/internal/packaging/reconcile")
    def reconcile(value: ReconcileInput):
        with available_slot():
            return _response(reconcile_packaging(value.value(), roots=settings.roots))

    @application.post("/internal/packaging/artifact")
    def artifact(value: ArtifactInput, request: Request):
        from app.packaging_download import RetainedFileResponse
        from app.packaging_plan import _relative
        if not _relative(value.path) or "range" in request.headers:
            raise HTTPException(422, {"code": "PACKAGING_REQUEST_INVALID"})
        return RetainedFileResponse(slot=slot, selection=value, artifact_root=settings.roots.artifacts)

    return application


app = create_packaging_app()
