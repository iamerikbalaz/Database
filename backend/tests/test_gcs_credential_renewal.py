"""Request-level token renewal against synthetic storage; no credential discovery."""
import asyncio
from contextlib import aclosing

import pytest
from pydantic import SecretStr

from app.gcs_contract import GcsError
from test_gcs_client import TOKEN, install


def test_each_storage_request_uses_the_latest_credential_without_extra_storage_writes(monkeypatch):
    client, storage = install(monkeypatch)
    reads = 0; observed = []
    async def provider():
        nonlocal reads
        reads += 1
        return SecretStr("synthetic-renewed-credential-" + str(reads).zfill(12))
    original = storage.handle
    def inspect(request):
        observed.append(request.headers["authorization"])
        # Reuse the strict storage protocol fixture after independently checking
        # the rotating header; this replaces only its fixed synthetic credential.
        request.headers["authorization"] = "Bearer " + TOKEN
        return original(request)
    storage.handle = inspect; client._token_provider = provider
    async def run():
        async with aclosing(storage.source()) as source:
            return await client.upload(storage.spec, source)
    assert asyncio.run(run()).spec == storage.spec
    assert reads == len(storage.requests) + 1
    assert observed == ["Bearer synthetic-renewed-credential-" + str(index).zfill(12) for index in range(2, reads + 1)]
    # The final PUT returns metadata; the two GETs read bytes and recheck metadata.
    assert [request.method for request in storage.requests] == ["POST", "PUT", "GET", "GET"]


@pytest.mark.parametrize("failure", ["provider", "revoked"])
def test_renewal_failure_after_upload_intent_stops_before_next_storage_write(monkeypatch, failure):
    client, storage = install(monkeypatch); reads = 0; revoked = False
    async def provider():
        nonlocal reads, revoked
        reads += 1
        if reads == 3:
            if failure == "provider": raise RuntimeError("Synthetic private credential diagnostic")
            revoked = True
        return SecretStr(TOKEN)
    async def guard():
        if revoked: raise RuntimeError("Synthetic revoked authority")
    client._token_provider = provider
    async def run():
        async with aclosing(storage.source()) as source:
            return await client.upload(storage.spec, source, operation_guard=guard)
    with pytest.raises(GcsError, match="^GCS_CREDENTIAL_UNAVAILABLE$" if failure == "provider" else "^GCS_OPERATION_BLOCKED$"):
        asyncio.run(run())
    assert [request.method for request in storage.requests] == ["POST"]
    assert all(stream.closed for stream in storage.streams)
    assert not storage.complete
