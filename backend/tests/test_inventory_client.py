import hashlib
import json

import httpx
import pytest

from app.inventory_client import InventoryClientError, SourceInventory, WorkerInventoryClient
from test_material_operations import StreamingResponse


def inventory_payload(identity="SAFE_0001_G03"):
    value = {"schema_version": 1, "folder_name": identity, "master_resolution": "4K",
             "master_last_modified_at": "2026-03-05T12:00:00+00:00", "policy": "CURRENT_ON_OR_AFTER_2026_03_04",
             "entries": [{"path": "4K", "kind": "directory", "size": 0, "sha256": None},
                         {"path": f"4K/{identity}_COL_4K.png", "kind": "file", "size": 3, "sha256": hashlib.sha256(b"map").hexdigest()}],
             "total_bytes": 3}
    return rehash(value)


def rehash(value):
    canonical = {key: value[key] for key in ("schema_version", "folder_name", "master_resolution", "policy", "entries")}
    value["source_revision_hash"] = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    return value


def test_verified_wire_contract_and_transport(monkeypatch):
    payload = inventory_payload()
    response = StreamingResponse([json.dumps(payload).encode()])
    def stream(method, url, **kwargs):
        assert method == "POST" and url == "http://worker:8080/internal/material-inventory"
        assert kwargs["json"] == {"folder_path": "library/SAFE_0001_G03"}
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return response
    monkeypatch.setattr(httpx, "stream", stream)
    result = WorkerInventoryClient("http://worker:8080").inventory("library/SAFE_0001_G03")
    assert result.source_revision_hash == payload["source_revision_hash"] and response.closed


@pytest.mark.parametrize("change", ["hash", "path", "absolute", "duplicate", "order", "total", "directory-size", "file-hash", "parent", "timestamp", "master", "unknown-field", "identity"])
def test_malformed_inventory_is_rejected_without_reflection(monkeypatch, change):
    payload = inventory_payload()
    entry = payload["entries"][1]
    if change == "hash": payload["source_revision_hash"] = "a" * 64
    elif change == "path": entry["path"] = "../SYNTHETIC_SECRET"
    elif change == "absolute": entry["path"] = "C:/SYNTHETIC_SECRET"
    elif change == "duplicate": payload["entries"].append(entry.copy())
    elif change == "order": payload["entries"].reverse()
    elif change == "total": payload["total_bytes"] = 99
    elif change == "directory-size": payload["entries"][0]["size"] = 4
    elif change == "file-hash": entry["sha256"] = None
    elif change == "parent": entry["path"] = "missing/map.png"
    elif change == "timestamp": payload["master_last_modified_at"] = "2026-03-05T12:00:00"
    elif change == "master": payload["master_resolution"] = "8K"
    elif change == "unknown-field": payload["raw_content"] = "SYNTHETIC_SECRET"
    else: payload["folder_name"] = "OTHER_0001_G03"
    if change != "hash": rehash(payload)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: StreamingResponse([json.dumps(payload).encode()]))
    with pytest.raises(InventoryClientError) as caught:
        WorkerInventoryClient("http://worker").inventory("SAFE_0001_G03")
    assert "SYNTHETIC_SECRET" not in str(caught.value) and caught.value.__cause__ is None


@pytest.mark.parametrize("body,code", [
    ({"detail": {"code": "INVENTORY_SOURCE_CHANGED"}}, "INVENTORY_SOURCE_CHANGED"),
    ({"detail": {"code": "SYNTHETIC_SECRET"}}, "INVENTORY_UNAVAILABLE"),
    ({"detail": "SYNTHETIC_SECRET"}, "INVENTORY_UNAVAILABLE"),
])
def test_error_codes_are_allowlisted(monkeypatch, body, code):
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: StreamingResponse([json.dumps(body).encode()], status_code=422))
    with pytest.raises(InventoryClientError) as caught:
        WorkerInventoryClient("http://worker").inventory("SAFE_0001_G03")
    assert caught.value.code == code


@pytest.mark.parametrize("declared", [True, False])
def test_inventory_stream_is_bounded_even_when_content_length_lies(monkeypatch, declared):
    monkeypatch.setattr("app.inventory_client.MAX_INVENTORY_RESPONSE_BYTES", 10)
    response = StreamingResponse([b"x" * 11], content_length=11 if declared else 1)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: response)
    with pytest.raises(InventoryClientError): WorkerInventoryClient("http://worker").inventory("SAFE_0001_G03")
    assert response.closed


def test_missing_master_is_a_valid_inventory_but_not_technical_readiness():
    value = inventory_payload(); value.update(master_resolution=None, master_last_modified_at=None, policy=None, entries=[], total_bytes=0)
    assert SourceInventory.model_validate_json(json.dumps(rehash(value))).master_resolution is None
