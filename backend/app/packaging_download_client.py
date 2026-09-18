"""Independent bounded streaming validation of a retained worker artifact."""
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
import hashlib
import json
import re
import time

import anyio
import httpx

from app.packaging_client import PackagingClientError
from app.packaging_contract import PreparedPackaging, DispatchedPackagingResult, RetainedFile

CHUNK_BYTES = 64 * 1024
MAX_ERROR_BYTES = 4096
TRANSFER_SECONDS = 610


def download_selection(prepared, report, result, path):
    """Never use a caller's mutable model or path as the artifact authority."""
    try:
        bound = PreparedPackaging.model_validate_json(prepared.model_dump_json(warnings="error"))
        observed = DispatchedPackagingResult.model_validate_json(result.model_dump_json(warnings="error"))
        observed.verify_request(bound, report)
        if observed.stored is None: raise ValueError()
        item = next(item for item in observed.stored.payload.files if item.path == path)
        return item, {"operation_id": observed.operation_id, "request_hash": bound.request_hash,
            "plan_hash": observed.stored.plan_hash, "proof_sha256": observed.stored.proof_sha256,
            **item.model_dump(mode="json")}
    except (ValueError, TypeError, AttributeError, KeyError, ArithmeticError, RecursionError, StopIteration):
        raise PackagingClientError("PACKAGING_DOWNLOAD_UNAVAILABLE") from None


@dataclass
class ArtifactStream:
    file: RetainedFile
    proof_sha256: str
    response: httpx.Response
    deadline: float
    used: bool = False

    async def chunks(self):
        if self.used: raise PackagingClientError("PACKAGING_DOWNLOAD_UNAVAILABLE")
        self.used = True
        count = 0; digest = hashlib.sha256(); pending = b""
        try:
            async for block in self.response.aiter_raw(chunk_size=CHUNK_BYTES):
                count += len(block)
                if count > self.file.size or time.monotonic() >= self.deadline: raise ValueError()
                digest.update(block)
                if pending: yield pending
                pending = block
            if count != self.file.size or digest.hexdigest() != self.file.sha256 or time.monotonic() >= self.deadline:
                raise ValueError()
            if pending: yield pending
        except (httpx.HTTPError, ValueError, TypeError, ArithmeticError):
            raise PackagingClientError("PACKAGING_DOWNLOAD_UNAVAILABLE") from None


@asynccontextmanager
async def open_worker_artifact(client, prepared, report, result, path):
    client._enabled()
    item, selection = download_selection(prepared, report, result, path)
    seconds = min(client.timeout_seconds, TRANSFER_SECONDS)
    deadline = time.monotonic() + seconds
    try:
        # No redirects/proxies, automatic retries, ranges or transparent decoding.
        with anyio.fail_after(seconds):
            lifetime = AsyncExitStack()
            try:
                connection = await lifetime.enter_async_context(httpx.AsyncClient(timeout=httpx.Timeout(seconds, connect=min(5, seconds)),
                    trust_env=False, follow_redirects=False))
                response = await lifetime.enter_async_context(connection.stream("POST", client.base_url + "/internal/packaging/artifact",
                    content=json.dumps(selection, ensure_ascii=True, separators=(",", ":")).encode(),
                    headers={"Authorization": "Bearer " + client._token.get_secret_value(), "Content-Type": "application/json",
                        "Accept-Encoding": "identity"}))
                if response.headers.get("Content-Encoding", "identity").lower() != "identity": raise ValueError()
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if response.status_code != 200:
                    if response.status_code not in {401, 404, 408, 409, 413, 422, 503} or content_type != "application/json": raise ValueError()
                    body = bytearray()
                    async for block in response.aiter_raw(chunk_size=MAX_ERROR_BYTES):
                        if len(body) + len(block) > MAX_ERROR_BYTES or time.monotonic() >= deadline: raise ValueError()
                        body.extend(block)
                    error = json.loads(body)
                    code = error.get("detail", {}).get("code") if isinstance(error, dict) and isinstance(error.get("detail"), dict) else None
                    raise PackagingClientError(code)
                length = response.headers.get("Content-Length", "")
                if (content_type != "application/octet-stream" or re.fullmatch(r"[0-9]{1,11}", length) is None
                        or int(length) != item.size or response.headers.get("X-Packaging-Proof-Sha256") != selection["proof_sha256"]
                        or response.headers.get("X-Packaging-File-Sha256") != item.sha256 or time.monotonic() >= deadline):
                    raise ValueError()
                yield ArtifactStream(item, selection["proof_sha256"], response, deadline)
            finally:
                with anyio.CancelScope(shield=True):
                    await lifetime.aclose()
    except PackagingClientError: raise
    except (httpx.HTTPError, ValueError, TypeError, AttributeError, ArithmeticError, RecursionError, TimeoutError):
        raise PackagingClientError("PACKAGING_DOWNLOAD_UNAVAILABLE") from None
