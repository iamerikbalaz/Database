"""Offline ADC lifecycle and real Google SDK refresh with synthetic credentials."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
from threading import Event
import time
from uuid import uuid4

import anyio
import google.auth
from google.oauth2.credentials import Credentials
import httpx
import pytest

from app import gcs_credentials as module
from app.core.config import Settings
from app.gcs_client import GcsClient
from app.gcs_contract import GcsError
from test_gcs_client import spec


def secret(): return uuid4().hex + uuid4().hex


class SyntheticCredentials:
    universe_domain = "googleapis.com"
    def __init__(self):
        self.token = None; self.expiry = None; self.refreshes = 0; self.callback = None
    def refresh(self, request):
        self.refreshes += 1
        if self.callback: self.callback()
        self.token = secret(); self.expiry = datetime.now(timezone.utc) + timedelta(hours=1)


def install(monkeypatch, credentials=None):
    value = credentials or SyntheticCredentials(); discoveries = []
    def discover(request): discoveries.append(request); return value
    monkeypatch.setattr(module, "_discover", discover)
    original = httpx.Client
    def offline(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original(**kwargs, transport=httpx.MockTransport(lambda request: pytest.fail("Unexpected credential HTTP")))
    monkeypatch.setattr(httpx, "Client", offline)
    return module.AdcTokenProvider(), value, discoveries


def test_explicit_configuration_does_not_discover_credentials_until_authorized_io(monkeypatch):
    monkeypatch.setattr(module, "_discover", lambda request: pytest.fail("Ambient discovery must not run"))
    settings = Settings(_env_file=None, gcs_auth_mode="adc")
    client = GcsClient.from_settings(settings)
    with pytest.raises(GcsError, match="GCS_DISABLED"): asyncio.run(client.reconcile(spec()))
    configured = settings.model_copy(update={"gcs_enabled": True, "gcs_bucket_name": "synthetic-staging", "gcs_staging_prefix": "isolated/contracts"})
    client = GcsClient.from_settings(configured)
    assert isinstance(client._token_provider, module.AdcTokenProvider)
    async def denied(): raise RuntimeError("Synthetic revoked authority")
    with pytest.raises(GcsError, match="GCS_OPERATION_BLOCKED"):
        asyncio.run(client.reconcile(spec(), operation_guard=denied))


@pytest.mark.parametrize("changes", [{"gcs_auth_mode": "ambient"}, {"gcs_auth_mode": "adc", "gcs_access_token": "synthetic"}])
def test_invalid_or_ambiguous_modes_fail_configuration(changes):
    with pytest.raises(ValueError):
        Settings(_env_file=None, gcs_enabled=True, gcs_bucket_name="synthetic-staging", gcs_staging_prefix="isolated/contracts", **changes)


def test_cached_token_is_reused_and_refreshed_before_expiry(monkeypatch):
    provider, credentials, discoveries = install(monkeypatch)
    first = asyncio.run(provider())
    assert asyncio.run(provider()) == first and credentials.refreshes == 1 and len(discoveries) == 1
    credentials.expiry = datetime.now(timezone.utc) + timedelta(seconds=119)
    assert asyncio.run(provider()) != first and credentials.refreshes == 2 and len(discoveries) == 1
    assert first.get_secret_value() not in repr(first)


def test_failed_refresh_never_uses_expired_token_or_logs_private_diagnostics(monkeypatch, caplog):
    provider, credentials, _ = install(monkeypatch)
    prior = asyncio.run(provider())
    credentials.expiry = datetime.now(timezone.utc) - timedelta(seconds=1)
    diagnostic = secret()
    def fail():
        logging.getLogger("google.auth.synthetic.new.logger").error(diagnostic)
        raise RuntimeError(diagnostic)
    credentials.callback = fail
    with caplog.at_level(logging.DEBUG), pytest.raises(GcsError, match="^GCS_CREDENTIAL_UNAVAILABLE$") as caught:
        asyncio.run(provider())
    assert diagnostic not in str(caught.value) and diagnostic not in caplog.text and prior.get_secret_value() not in caplog.text
    assert provider._credentials is None
    logging.getLogger("google.auth.synthetic.new.logger").warning("Unrelated diagnostics remain enabled")
    assert "Unrelated diagnostics remain enabled" in caplog.text


@pytest.mark.parametrize("change", ["missing-expiry", "near-expiry", "empty-token", "newline-token", "foreign-universe"])
def test_unusable_refreshed_credentials_never_escape(monkeypatch, change):
    provider, credentials, _ = install(monkeypatch)
    def refresh(request):
        credentials.token = secret(); credentials.expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        if change == "missing-expiry": credentials.expiry = None
        if change == "near-expiry": credentials.expiry = datetime.now(timezone.utc) + timedelta(seconds=30)
        if change == "empty-token": credentials.token = ""
        if change == "newline-token": credentials.token += "\n"
    credentials.refresh = refresh
    if change == "foreign-universe": credentials.universe_domain = "invalid.example"
    with pytest.raises(GcsError, match="^GCS_CREDENTIAL_UNAVAILABLE$"): asyncio.run(provider())


@pytest.mark.parametrize("cancel", [False, True])
def test_refresh_keeps_its_lock_until_worker_cleanup_even_after_cancellation(monkeypatch, cancel):
    provider, credentials, _ = install(monkeypatch); entered = Event(); release = Event()
    def hold():
        entered.set()
        if not release.wait(5): raise TimeoutError("Synthetic refresh was not released")
    credentials.callback = hold
    async def run():
        pending = asyncio.create_task(provider())
        try:
            assert await anyio.to_thread.run_sync(lambda: entered.wait(3))
            if cancel:
                pending.cancel()
                with pytest.raises(asyncio.CancelledError): await pending
            with pytest.raises(GcsError, match="GCS_CREDENTIAL_UNAVAILABLE"): await provider()
        finally: release.set()
        if not cancel: await pending
        with anyio.fail_after(3):
            while provider._lock.locked(): await anyio.sleep(0.01)
        assert (await provider()).get_secret_value() == credentials.token
        assert credentials.refreshes == 1
    asyncio.run(run())


def test_provider_deadline_does_not_report_a_late_token_as_success(monkeypatch):
    provider, credentials, _ = install(monkeypatch)
    monkeypatch.setattr(module, "REFRESH_SECONDS", 0.05)
    credentials.callback = lambda: time.sleep(0.12)
    async def run():
        with pytest.raises(GcsError, match="GCS_CREDENTIAL_UNAVAILABLE"): await provider()
        with anyio.fail_after(2):
            while provider._lock.locked(): await anyio.sleep(0.01)
        assert provider._credentials is None
    asyncio.run(run())


def test_real_google_sdk_discovery_protocol_refreshes_through_private_mock_http(monkeypatch):
    credentials = Credentials(token=None, refresh_token=secret(), client_id="synthetic-client", client_secret=secret(), token_uri="https://oauth2.googleapis.com/token")
    discoveries = []; requests = []; issued = []
    def discover(**kwargs): discoveries.append(kwargs); return credentials, None
    monkeypatch.setattr(google.auth, "default", discover)
    original = httpx.Client
    def exchange(request):
        requests.append(request)
        assert request.method == "POST" and request.url == "https://oauth2.googleapis.com/token"
        issued.append(secret())
        return httpx.Response(200, headers={"Content-Type": "application/json"}, stream=httpx.ByteStream(json.dumps({"access_token": issued[-1], "expires_in": 3600, "token_type": "Bearer"}).encode()))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(exchange)))
    provider = module.AdcTokenProvider()
    first = asyncio.run(provider()); assert first.get_secret_value() == issued[0]
    assert asyncio.run(provider()) == first and len(requests) == 1
    credentials.expiry = datetime.now(timezone.utc).replace(tzinfo=None)
    assert asyncio.run(provider()).get_secret_value() == issued[1] and len(requests) == 2
    assert discoveries[0]["scopes"] == [module.SCOPE] and isinstance(discoveries[0]["request"], module._Request)


def test_real_sdk_loads_only_the_explicit_synthetic_adc_file(monkeypatch, tmp_path):
    location = tmp_path / "synthetic-adc.json"
    location.write_text(json.dumps({"type": "authorized_user", "client_id": "synthetic-client",
        "client_secret": secret(), "refresh_token": secret()}), encoding="utf-8")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(location))
    issued = secret(); calls = []; original = httpx.Client
    def exchange(request):
        calls.append(request)
        assert request.url == "https://oauth2.googleapis.com/token" and request.method == "POST"
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps({"access_token": issued, "expires_in": 3600}).encode()))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(exchange)))
    assert asyncio.run(module.AdcTokenProvider()()).get_secret_value() == issued
    assert len(calls) == 1


def test_compute_sdk_metadata_refresh_uses_only_the_bounded_supplied_transport(monkeypatch):
    from google.auth.compute_engine import Credentials as ComputeCredentials
    from google.auth.transport import requests as google_requests
    monkeypatch.setattr(google_requests, "Request", lambda *args, **kwargs: pytest.fail("SDK bypassed supplied metadata transport"))
    monkeypatch.setattr(google.auth, "default", lambda **kwargs: (ComputeCredentials(scopes=[module.SCOPE]), None))
    calls = []; issued = secret(); original = httpx.Client
    def metadata(request):
        calls.append(request)
        assert request.url.scheme == "http" and request.url.host == "metadata.google.internal"
        assert request.headers["metadata-flavor"] == "Google"
        value = ({"access_token": issued, "expires_in": 3600} if request.url.path.endswith("/token")
            else {"email": "synthetic@example.invalid", "scopes": [module.SCOPE], "aliases": ["default"]})
        return httpx.Response(200, headers={"Content-Type": "application/json"}, stream=httpx.ByteStream(json.dumps(value).encode()))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(metadata)))
    assert asyncio.run(module.AdcTokenProvider()()).get_secret_value() == issued
    assert len(calls) == 2 and calls[-1].url.path.endswith("/token")


@pytest.mark.parametrize("rejected", [False, True])
def test_service_account_sdk_signs_and_handles_private_refresh_errors_offline(monkeypatch, caplog, rejected):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from google.oauth2 import service_account
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    credentials = service_account.Credentials.from_service_account_info({
        "client_email": "synthetic@example.invalid", "token_uri": "https://oauth2.googleapis.com/token",
        "private_key": signing_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
    }, scopes=[module.SCOPE])
    monkeypatch.setattr(google.auth, "default", lambda **kwargs: (credentials, None))
    issued = secret(); diagnostic = secret(); calls = []; original = httpx.Client
    def exchange(request):
        calls.append(request)
        assert request.method == "POST" and request.url == "https://oauth2.googleapis.com/token"
        assert b"assertion=" in request.content and b"jwt-bearer" in request.content
        value = {"error": "invalid_grant", "error_description": diagnostic} if rejected else {"access_token": issued, "expires_in": 3600}
        return httpx.Response(400 if rejected else 200, stream=httpx.ByteStream(json.dumps(value).encode()))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(exchange)))
    with caplog.at_level(logging.DEBUG):
        if rejected:
            with pytest.raises(GcsError, match="^GCS_CREDENTIAL_UNAVAILABLE$") as caught:
                asyncio.run(module.AdcTokenProvider()())
            assert diagnostic not in str(caught.value)
        else: assert asyncio.run(module.AdcTokenProvider()()).get_secret_value() == issued
    assert len(calls) == 1 and diagnostic not in caplog.text and issued not in caplog.text


@pytest.mark.parametrize("url", ["file:///secret", "http://127.0.0.1/token", "https://name:password@example.invalid/token", "https://example.invalid/token#fragment"])
def test_credential_transport_rejects_non_https_or_embedded_capabilities(url):
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("Unsafe request"))) as client:
        request = module._Request(client, time.monotonic() + 10)
        with pytest.raises(GcsError, match="GCS_CREDENTIAL_UNAVAILABLE"): request(url)


@pytest.mark.parametrize("kind", ["redirect", "oversize", "encoding"])
def test_credential_response_is_bounded_and_never_follows_redirects(kind):
    def response(request):
        return httpx.Response(302 if kind == "redirect" else 200,
            headers={"Location": "https://other.invalid/token", "Content-Encoding": "gzip" if kind == "encoding" else "identity"},
            stream=httpx.ByteStream(b"x" * (65537 if kind == "oversize" else 1)))
    with httpx.Client(transport=httpx.MockTransport(response), follow_redirects=False) as client:
        request = module._Request(client, time.monotonic() + 10)
        with pytest.raises(GcsError): request("https://oauth2.googleapis.com/token")
