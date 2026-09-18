"""Proof-bound retained artifacts and frozen CSV streams for internal staging.

The caller must commit transfer intent before opening a source. This module does
not claim durable ownership, access the NAS, mutate source files or publish data.
"""
from contextlib import AsyncExitStack, aclosing, asynccontextmanager
from dataclasses import replace
import hashlib
import json

import anyio

from app.gcs_batch import PlannedObject, StagingPlan
from app.gcs_client import _OperationGuard, SOURCE_BLOCK_BYTES
from app.gcs_contract import GcsError, GcsObjectSpec
from app.packaging_contract import PreparedPackaging, DispatchedPackagingResult


def _require(condition, code="GCS_SELECTION_INVALID"):
    if not condition: raise GcsError(code)


class StagingSources:
    def __init__(self, prepared, worker, *, operation_guard):
        _require(callable(operation_guard), "GCS_OPERATION_BLOCKED")
        self.worker = worker
        self._authorize = operation_guard
        try:
            self.plan = StagingPlan.model_validate(prepared.plan.model_dump(mode="python", warnings="error"))
            self._csv = prepared.csv_bytes
            _require(type(self._csv) is bytes and 1 <= len(self._csv) <= 32 * 1024**2)
            bindings = {item.material_id: item for item in self.plan.body.materials}
            _require(len(prepared.packages) == len(bindings))
            self._packages = {}
            files = [PlannedObject(relative_path="publication.csv", material_id=None, source_path=None,
                size=len(self._csv), sha256=hashlib.sha256(self._csv).hexdigest())]
            for package in prepared.packages:
                identifier = str(package.material_id)
                _require(identifier in bindings and identifier not in self._packages)
                request = PreparedPackaging.model_validate(package.prepared.model_dump(mode="python", warnings="error"))
                result = DispatchedPackagingResult.model_validate(package.result.model_dump(mode="python", warnings="error"))
                report = json.loads(json.dumps(package.report, ensure_ascii=True, allow_nan=False))
                result.verify_request(request, report)
                bound = bindings[identifier]
                _require(result.status == "READY" and result.terminal == "OPEN" and result.stored is not None)
                _require(bound.execution_id == result.operation_id and bound.batch_item_sha256 == package.batch_item_sha256
                    and bound.worker_request_sha256 == request.request_hash
                    and bound.packaging_proof_sha256 == result.stored.proof_sha256)
                self._packages[identifier] = replace(package, prepared=request, result=result, report=report)
                files.extend(PlannedObject(relative_path=f"materials/{identifier}/{item.path}",
                    material_id=identifier, source_path=item.path, size=item.size, sha256=item.sha256)
                    for item in result.stored.payload.files)
            _require(tuple(sorted(files, key=lambda item: item.relative_path)) == self.plan.body.objects)
            self._objects = {item.relative_path: item for item in self.plan.body.objects}
            self._specs = {item.relative_path: item for item in self.plan.specifications()}
        except GcsError: raise
        except Exception:
            raise GcsError("GCS_SELECTION_INVALID") from None

    async def _csv_blocks(self):
        for start in range(0, len(self._csv), SOURCE_BLOCK_BYTES):
            yield self._csv[start:start + SOURCE_BLOCK_BYTES]

    async def _verified(self, chunks, spec, guard):
        count = 0; digest = hashlib.sha256(); pending = b""
        try:
            async for block in chunks:
                _require(type(block) is bytes and 1 <= len(block) <= SOURCE_BLOCK_BYTES, "GCS_SOURCE_CHANGED")
                count += len(block)
                _require(count <= spec.size, "GCS_SOURCE_CHANGED")
                digest.update(block)
                await guard.progress(len(block))
                if pending: yield pending
                pending = block
            _require(count == spec.size and digest.hexdigest() == spec.sha256, "GCS_SOURCE_CHANGED")
            await guard.require()
            if pending: yield pending
        except GcsError: raise
        except Exception:
            raise GcsError("GCS_SOURCE_UNAVAILABLE") from None

    @asynccontextmanager
    async def open(self, selection):
        try:
            spec = GcsObjectSpec.model_validate(selection.model_dump(mode="python", warnings="error"))
            _require(spec == self._specs.get(spec.relative_path))
            item = self._objects[spec.relative_path]
        except GcsError: raise
        except Exception:
            raise GcsError("GCS_SELECTION_INVALID") from None
        guard = _OperationGuard(self._authorize)
        await guard.require()
        lifetime = AsyncExitStack()
        try:
            if item.material_id is None:
                chunks = self._csv_blocks()
            else:
                package = self._packages[item.material_id]
                try:
                    upstream = await lifetime.enter_async_context(self.worker.open_artifact(
                        package.prepared, package.report, package.result, item.source_path))
                    _require(upstream.file.path == item.source_path and upstream.file.size == item.size
                        and upstream.file.sha256 == item.sha256
                        and upstream.proof_sha256 == package.result.stored.proof_sha256, "GCS_SOURCE_CHANGED")
                    chunks = upstream.chunks()
                except GcsError: raise
                except Exception:
                    raise GcsError("GCS_SOURCE_UNAVAILABLE") from None
            await guard.require()
            await lifetime.enter_async_context(aclosing(chunks))
            source = await lifetime.enter_async_context(aclosing(self._verified(chunks, spec, guard)))
            yield source
        finally:
            with anyio.CancelScope(shield=True):
                await lifetime.aclose()
