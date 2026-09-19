"""Bounded, non-retrying transport to the separate private packaging service."""
import json
import re
import time
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from app.packaging_contract import PackagingResult, PreparedPackaging, PackagingDispatch, DispatchedPackagingResult
from app.packaging_retirement_contract import PackagingRetirementCommand, PackagingRetirementReceipt

MAX_REQUEST_BYTES = 8 * 1024**2
MAX_RESPONSE_BYTES = 32 * 1024**2 + 65536
PACKAGING_CODES = frozenset({
    "PACKAGING_DOWNLOAD_UNAVAILABLE", "PACKAGING_STORE_UNKNOWN_OPERATION", "PACKAGING_STORE_FILE_NOT_FOUND",
    "PACKAGING_STORE_PROOF_MISMATCH", "PACKAGING_STORE_FILE_MISMATCH", "PACKAGING_STORE_REQUEST_CONFLICT",
    "PACKAGING_DISPATCH_REQUIRED", "PACKAGING_DISPATCH_INVALID", "PACKAGING_DISPATCH_ORDER", "PACKAGING_DISPATCH_STALE",
    "PACKAGING_DISPATCH_CONFLICT", "PACKAGING_EXECUTION_CLOSED",
    "PACKAGING_SERVICE_DISABLED", "PACKAGING_SERVICE_UNAVAILABLE", "PACKAGING_SERVICE_UNAUTHORIZED", "PACKAGING_SERVICE_BUSY",
    "PACKAGING_REQUEST_INVALID", "PACKAGING_REQUEST_TOO_LARGE", "PACKAGING_REQUEST_TIMEOUT",
    "PACKAGING_EXECUTION_NOT_FOUND", "PACKAGING_EXECUTION_REQUEST_CONFLICT", "PACKAGING_EXECUTION_REPORT_CHANGED",
    "PACKAGING_EXECUTION_PLAN_CHANGED", "PACKAGING_EXECUTION_ATTEMPT_LIMIT", "PACKAGING_EXECUTION_TIME_LIMIT",
    "PACKAGING_EXECUTION_RECOVERY_REQUIRED", "PACKAGING_EXECUTION_CORRUPT_STATE", "PACKAGING_EXECUTION_ROOT_CHANGED",
    "PACKAGING_EXECUTION_WORKSPACE_CHANGED", "PACKAGING_EXECUTION_RESULT_CHANGED", "PACKAGING_EXECUTION_RESULT_MISSING",
    "PACKAGING_EXECUTION_UNKNOWN_OPERATION", "PACKAGING_EXECUTION_UNKNOWN_FILE", "PACKAGING_EXECUTION_UNKNOWN_WORKSPACE_FILE",
    "PACKAGING_SOURCE_CHANGED", "PACKAGING_SOURCE_CHECK_FAILED", "PACKAGING_LEASE_BUSY", "PACKAGING_LEASE_CHANGED",
    "PACKAGING_STORE_BUSY", "PACKAGING_STORE_CORRUPT_STATE", "PACKAGING_STORE_ARTIFACT_CHANGED",
    "PACKAGING_STORE_ARTIFACT_MISSING", "PACKAGING_STORE_RECOVERY_REQUIRED",
    "PACKAGING_STAGE_SIZE_LIMIT", "PACKAGING_ASSEMBLY_SIZE_LIMIT", "PACKAGING_STORE_SIZE_LIMIT",
    "PACKAGING_STAGE_TIME_LIMIT", "PACKAGING_ASSEMBLY_TIME_LIMIT", "PACKAGING_STORE_TIME_LIMIT",
    "PACKAGING_CONVERSION_RUNTIME_UNAVAILABLE", "PACKAGING_CONVERSION_POLICY_MISMATCH", "PACKAGING_CONVERSION_BUSY",
    "PACKAGING_STORE_RETIRED", "PACKAGING_RETIREMENT_DISABLED", "PACKAGING_RETIREMENT_INVALID",
    "PACKAGING_RETIREMENT_NOT_READY", "PACKAGING_RETIREMENT_PROOF_MISMATCH", "PACKAGING_RETIREMENT_REQUEST_CONFLICT",
    "PACKAGING_RETIREMENT_UNKNOWN_OPERATION", "PACKAGING_RETIREMENT_CORRUPT_STATE", "PACKAGING_RETIREMENT_FAILED",
    "PACKAGING_RETIREMENT_FILE_CHANGED", "PACKAGING_RETIREMENT_DIRECTORY_CHANGED", "PACKAGING_RETIREMENT_TREE_CHANGED",
    "PACKAGING_RETIREMENT_ROOT_CHANGED",
})


class PackagingClientError(RuntimeError):
    def __init__(self, code="PACKAGING_UNAVAILABLE"):
        self.code = code if code in PACKAGING_CODES else "PACKAGING_UNAVAILABLE"
        super().__init__("Packaging result could not be verified.")


class PackagingClient(Protocol):
    def retire(self, prepared: PreparedPackaging, report: dict, accepted: DispatchedPackagingResult,
        command: PackagingRetirementCommand) -> PackagingRetirementReceipt: ...
    def open_artifact(self, prepared, report, result, path): ...
    def prepare(self, payload: dict) -> PreparedPackaging: ...
    def dispatch(self, prepared: PreparedPackaging, report: dict, command: PackagingDispatch) -> DispatchedPackagingResult: ...
    def execute(self, prepared: PreparedPackaging, report: dict, *, retry: bool = False) -> PackagingResult: ...
    def reconcile(self, prepared: PreparedPackaging, report: dict) -> PackagingResult: ...


class WorkerPackagingClient:
    def __init__(self, base_url: str, *, token: SecretStr | None = None, enabled: bool = False, timeout_seconds: float = 3630):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"} or "\\" in base_url
                or parsed.port is not None and not 1 <= parsed.port <= 65535):
            raise ValueError("Invalid packaging service URL")
        if type(timeout_seconds) not in {int, float} or not 0 < timeout_seconds <= 3660:
            raise ValueError("Invalid packaging service timeout")
        self.base_url = base_url.rstrip("/")
        self._token = token; self.enabled = enabled
        self.timeout_seconds = timeout_seconds
        self.timeout = httpx.Timeout(timeout_seconds, connect=min(5, timeout_seconds))

    def _enabled(self):
        value = self._token.get_secret_value() if isinstance(self._token, SecretStr) else None
        if (self.enabled is not True or not isinstance(value, str) or not 32 <= len(value) <= 256
                or not value.isascii() or not all(33 <= ord(char) <= 126 for char in value)):
            raise PackagingClientError("PACKAGING_SERVICE_DISABLED")

    def open_artifact(self, prepared, report, result, path):
        from app.packaging_download_client import open_worker_artifact
        return open_worker_artifact(self, prepared, report, result, path)

    def _request(self, action, payload, *, response_limit=None, time_limit=None):
        self._enabled()
        try:
            if response_limit is None: response_limit = MAX_RESPONSE_BYTES
            body = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()
            if len(body) > MAX_REQUEST_BYTES: raise PackagingClientError("PACKAGING_REQUEST_TOO_LARGE")
            seconds = self.timeout_seconds if time_limit is None else min(self.timeout_seconds, time_limit)
            deadline = time.monotonic() + seconds
            with httpx.stream("POST", self.base_url + "/internal/packaging/" + action, content=body,
                    headers={"Authorization": "Bearer " + self._token.get_secret_value(), "Content-Type": "application/json"},
                    timeout=httpx.Timeout(seconds, connect=min(5, seconds)), trust_env=False, follow_redirects=False) as response:
                if response.headers.get("Content-Encoding", "identity").lower() != "identity": raise ValueError()
                if response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json": raise ValueError()
                declared = response.headers.get("Content-Length")
                if declared is not None and (re.fullmatch(r"[0-9]{1,10}", declared) is None or int(declared) > response_limit): raise ValueError()
                chunks = []; size = 0
                for chunk in response.iter_bytes(chunk_size=min(65536, response_limit + 1)):
                    size += len(chunk)
                    if size > response_limit or time.monotonic() >= deadline: raise ValueError()
                    chunks.append(chunk)
                if declared is not None and size != int(declared): raise ValueError()
                content = b"".join(chunks)
                if response.status_code != 200:
                    if response.status_code in {401, 404, 408, 409, 413, 422, 503}:
                        failure = json.loads(content)
                        if isinstance(failure, dict) and isinstance(failure.get("detail"), dict):
                            raise PackagingClientError(failure["detail"].get("code"))
                    raise PackagingClientError()
                return content
        except PackagingClientError: raise
        except (httpx.HTTPError, ValueError, TypeError, AttributeError, ArithmeticError, RecursionError):
            # A timeout may follow committed filesystem writes. No implicit retry,
            # cancellation claim, raw response or chained parser diagnostics.
            raise PackagingClientError() from None

    def prepare(self, payload):
        self._enabled()
        try:
            result = PreparedPackaging.model_validate_json(self._request("prepare", payload))
            result.verify_preparation(payload)
            return result
        except PackagingClientError: raise
        except (ValueError, TypeError, AttributeError, ArithmeticError, KeyError, RecursionError):
            raise PackagingClientError() from None

    def _result(self, action, prepared, report, retry=False):
        self._enabled()
        try:
            # Revalidate models because frozen model objects can still contain
            # mutable list values. An old prepared object is not an authority.
            bound = PreparedPackaging.model_validate_json(prepared.model_dump_json())
            bound.request.verify_report(report)
            if type(retry) is not bool: raise ValueError()
            payload = bound.model_dump(mode="json")
            if action == "execute": payload.update(report=report, retry=retry)
            result = PackagingResult.model_validate_json(self._request(action, payload))
            result.verify_request(bound, report)
            return result
        except PackagingClientError: raise
        except (ValueError, TypeError, AttributeError, ArithmeticError, KeyError, RecursionError):
            raise PackagingClientError() from None

    def execute(self, prepared, report, *, retry=False): return self._result("execute", prepared, report, retry)

    def reconcile(self, prepared, report): return self._result("reconcile", prepared, report)

    def dispatch(self, prepared, report, command):
        """Application jobs use only this ordered boundary, including recovery."""
        self._enabled()
        try:
            bound = PreparedPackaging.model_validate_json(prepared.model_dump_json())
            bound.request.verify_report(report)
            action = PackagingDispatch.model_validate_json(command.model_dump_json())
            payload = {**bound.model_dump(mode="json"), "dispatch": action.model_dump(mode="json")}
            if action.action in {"EXECUTE", "RETRY"}: payload["report"] = report
            result = DispatchedPackagingResult.model_validate_json(self._request("dispatch", payload))
            result.verify_request(bound, report); result.verify_dispatch(action)
            return result
        except PackagingClientError: raise
        except (ValueError, TypeError, AttributeError, ArithmeticError, KeyError, RecursionError):
            raise PackagingClientError() from None

    def retire(self, prepared, report, accepted, command):
        """Caller commits durable authorization before this non-retrying command."""
        self._enabled()
        try:
            bound = PreparedPackaging.model_validate_json(prepared.model_dump_json())
            saved = DispatchedPackagingResult.model_validate_json(accepted.model_dump_json())
            saved.verify_request(bound, report)
            action = PackagingRetirementCommand.model_validate_json(command.model_dump_json())
            if (saved.status != "READY" or saved.stored is None or saved.terminal not in {"OPEN", "CLOSED"}
                    or saved.stored.proof_sha256 != action.proof_sha256): raise ValueError()
            payload = {**bound.model_dump(mode="json"), **action.model_dump(mode="json")}
            receipt = PackagingRetirementReceipt.model_validate_json(
                self._request("retire", payload, response_limit=4096, time_limit=150))
            receipt.verify(bound, saved, action)
            return receipt
        except PackagingClientError: raise
        except (ValueError, TypeError, AttributeError, ArithmeticError, KeyError, RecursionError):
            raise PackagingClientError() from None
