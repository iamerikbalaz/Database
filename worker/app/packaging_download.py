"""Bounded private transfer of one independently selected retained artifact."""
from contextlib import ExitStack
import hashlib
import os

import anyio
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from app.packaging_store import PackagingStoreError, open_retained_file

CHUNK_BYTES = 64 * 1024
TRANSFER_SECONDS = 600


class RetainedFileResponse(Response):
    """Keep the read lease through verification, disconnects and send failures.

    The last chunk is withheld until the transfer digest and final descriptor/path
    checks pass. A failed stream cannot look like a complete Content-Length body.
    No file path, source contents or dependency exception is used as diagnostics.
    """
    def __init__(self, *, slot, selection, artifact_root):
        super().__init__(content=b"", media_type="application/octet-stream")
        self.slot = slot
        self.selection = selection
        self.artifact_root = artifact_root

    async def __call__(self, scope, receive, send):
        started = False
        acquired = False
        files = ExitStack()

        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start": started = True
            await send(message)

        async def chunks(fd):
            count = 0
            digest = hashlib.sha256()
            pending = b""
            while True:
                block = await run_in_threadpool(os.read, fd, CHUNK_BYTES)
                if not block: break
                count += len(block)
                if count > self.selection.size: raise PackagingStoreError("PACKAGING_STORE_ARTIFACT_CHANGED")
                digest.update(block)
                if pending: yield pending
                pending = block
            if count != self.selection.size or digest.hexdigest() != self.selection.sha256:
                raise PackagingStoreError("PACKAGING_STORE_ARTIFACT_CHANGED")
            await run_in_threadpool(files.close)
            if pending: yield pending

        try:
            with anyio.fail_after(TRANSFER_SECONDS):
                acquired = self.slot.acquire(blocking=False)
                if not acquired:
                    await JSONResponse({"detail": {"code": "PACKAGING_SERVICE_BUSY"}}, status_code=503,
                        headers={"Retry-After": "1"})(scope, receive, tracked_send)
                    return
                selection = self.selection
                fd = await run_in_threadpool(files.enter_context, open_retained_file(
                    artifact_root=self.artifact_root, operation_id=selection.operation_uuid,
                    request_hash=selection.request_hash, plan_hash=selection.plan_hash,
                    expected_proof_sha256=selection.proof_sha256, path=selection.path,
                    expected_size=selection.size, expected_file_sha256=selection.sha256, max_seconds=120))
                response = StreamingResponse(chunks(fd), media_type="application/octet-stream", headers={
                    "Content-Length": str(selection.size), "Content-Disposition": 'attachment; filename="package.bin"',
                    "X-Packaging-Proof-Sha256": selection.proof_sha256, "X-Packaging-File-Sha256": selection.sha256,
                    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
                await response(scope, receive, tracked_send)
        except ClientDisconnect:
            # No receiver remains. Do not log the transport exception or claim
            # completion; the outer finally still closes both leases.
            return
        except (PackagingStoreError, OSError, TimeoutError) as error:
            if started: raise RuntimeError("Packaging transfer interrupted.") from None
            code = str(error) if isinstance(error, PackagingStoreError) else "PACKAGING_DOWNLOAD_UNAVAILABLE"
            allowed = {"PACKAGING_STORE_BUSY", "PACKAGING_STORE_RETIRED", "PACKAGING_STORE_UNKNOWN_OPERATION", "PACKAGING_STORE_FILE_NOT_FOUND",
                "PACKAGING_STORE_PROOF_MISMATCH", "PACKAGING_STORE_FILE_MISMATCH", "PACKAGING_STORE_REQUEST_CONFLICT", "PACKAGING_STORE_ARTIFACT_CHANGED",
                "PACKAGING_STORE_ARTIFACT_MISSING", "PACKAGING_STORE_TIME_LIMIT"}
            if code not in allowed: code = "PACKAGING_DOWNLOAD_UNAVAILABLE"
            status = 404 if code in {"PACKAGING_STORE_UNKNOWN_OPERATION", "PACKAGING_STORE_FILE_NOT_FOUND"} else (
                503 if code in {"PACKAGING_STORE_BUSY", "PACKAGING_STORE_TIME_LIMIT", "PACKAGING_DOWNLOAD_UNAVAILABLE"} else 409)
            await JSONResponse({"detail": {"code": code}}, status_code=status)(scope, receive, tracked_send)
        finally:
            # StreamingResponse cancels its producer on disconnect. Close the
            # outer-owned descriptor/lease even when that generator is suspended.
            with anyio.CancelScope(shield=True):
                try:
                    await run_in_threadpool(files.close)
                finally:
                    if acquired: self.slot.release()
