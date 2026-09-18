"""Opt-in ADC token renewal. No discovery happens at import or construction."""
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import math
from threading import Lock
import time
from urllib.parse import urlsplit

import anyio
import httpx
from pydantic import SecretStr

from app.gcs_contract import GcsError

SCOPE = "https://www.googleapis.com/auth/devstorage.read_write"
REFRESH_MARGIN = timedelta(seconds=120)
REFRESH_SECONDS = 30
_credential_io = ContextVar("gcs_credential_io", default=False)


class _PrivateCredentialFilter(logging.Filter):
    def filter(self, record):
        return not _credential_io.get()


_private_filter = _PrivateCredentialFilter()


def _protect_diagnostics():
    # SDK sub-loggers can be created during discovery. Filtering their existing
    # destination handlers also covers those new loggers, only in this context.
    loggers = [logging.getLogger(), *logging.Logger.manager.loggerDict.values()]
    handlers = {handler for logger in loggers if isinstance(logger, logging.Logger) for handler in logger.handlers}
    if logging.lastResort is not None: handlers.add(logging.lastResort)
    for handler in handlers: handler.addFilter(_private_filter)


def _discover(request):
    import google.auth
    from google.auth.compute_engine import Credentials as ComputeCredentials
    credentials, _ = google.auth.default(scopes=[SCOPE], request=request)
    if isinstance(credentials, ComputeCredentials):
        # This transport supports public Google Cloud only. Pin its universe using
        # the SDK's copy API; the lazy property otherwise opens its own unbounded
        # metadata transport instead of the request adapter supplied above.
        credentials = credentials.with_universe_domain("googleapis.com")
    return credentials


@dataclass(frozen=True)
class _Response:
    status: int
    headers: dict = field(repr=False)
    data: bytes = field(repr=False)


class _Request:
    """Google auth's callable transport protocol, with private bounded HTTP IO."""
    def __init__(self, client, deadline):
        self.client = client
        self.deadline = deadline
        self.calls = 0

    def __call__(self, url, method="GET", body=None, headers=None, timeout=120, **kwargs):
        self.calls += 1
        remaining = self.deadline - time.monotonic()
        parsed = urlsplit(url)
        metadata = (parsed.scheme == "http" and parsed.netloc in {"metadata.google.internal", "169.254.169.254"}
            and (parsed.path == "/" or parsed.path.startswith("/computeMetadata/v1/")))
        if (self.calls > 12 or remaining <= 0 or method not in {"GET", "POST"}
                or parsed.username or parsed.password or parsed.fragment
                or not parsed.hostname or not (parsed.scheme == "https" or metadata) or kwargs):
            raise GcsError("GCS_CREDENTIAL_UNAVAILABLE")
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
                raise GcsError("GCS_CREDENTIAL_UNAVAILABLE")
            remaining = min(remaining, timeout)
        with self.client.stream(method, url, content=body, headers=headers,
                timeout=httpx.Timeout(min(5, remaining))) as response:
            if (300 <= response.status_code < 400
                    or response.headers.get("content-encoding", "identity").lower() != "identity"):
                raise GcsError("GCS_CREDENTIAL_UNAVAILABLE")
            data = bytearray()
            for chunk in response.iter_raw(chunk_size=4096):
                if len(data) + len(chunk) > 65536 or time.monotonic() >= self.deadline:
                    raise GcsError("GCS_CREDENTIAL_UNAVAILABLE")
                data.extend(chunk)
            return _Response(response.status_code, dict(response.headers), bytes(data))


class AdcTokenProvider:
    """Cache one renewable credential; never fall back to a static token."""
    def __init__(self):
        self._lock = Lock()
        self._credentials = None

    async def __call__(self):
        try:
            with anyio.fail_after(REFRESH_SECONDS):
                # A cancelled caller may leave SDK work finishing in this thread.
                # The thread owns its lock through cleanup; a new caller fails busy.
                return await anyio.to_thread.run_sync(self._read, abandon_on_cancel=True)
        except Exception:
            raise GcsError("GCS_CREDENTIAL_UNAVAILABLE") from None

    @staticmethod
    def _fresh(credentials):
        expiry = getattr(credentials, "expiry", None)
        if not isinstance(expiry, datetime): return False
        if expiry.tzinfo is None: expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry > datetime.now(timezone.utc) + REFRESH_MARGIN

    def _token(self, credentials):
        raw = credentials.token
        if (not self._fresh(credentials) or not isinstance(raw, str)
                or not 32 <= len(raw) <= 8192 or not raw.isascii()
                or any(not 33 <= ord(char) <= 126 for char in raw)):
            raise GcsError("GCS_CREDENTIAL_UNAVAILABLE")
        return SecretStr(raw)

    def _read(self):
        if not self._lock.acquire(blocking=False): raise GcsError("GCS_CREDENTIAL_UNAVAILABLE")
        context = _credential_io.set(True)
        try:
            if self._credentials is not None and self._fresh(self._credentials):
                return self._token(self._credentials)
            _protect_diagnostics()
            deadline = time.monotonic() + REFRESH_SECONDS
            with httpx.Client(trust_env=False, follow_redirects=False, http2=False,
                    headers={"Accept-Encoding": "identity"}, timeout=5) as client:
                request = _Request(client, deadline)
                credentials = self._credentials or _discover(request)
                if getattr(credentials, "universe_domain", "googleapis.com") != "googleapis.com":
                    raise GcsError("GCS_CREDENTIAL_UNAVAILABLE")
                if not self._fresh(credentials): credentials.refresh(request)
                if time.monotonic() >= deadline:
                    raise GcsError("GCS_CREDENTIAL_UNAVAILABLE")
                token = self._token(credentials)
                self._credentials = credentials
                return token
        except Exception:
            self._credentials = None
            raise GcsError("GCS_CREDENTIAL_UNAVAILABLE") from None
        finally:
            _credential_io.reset(context)
            self._lock.release()
