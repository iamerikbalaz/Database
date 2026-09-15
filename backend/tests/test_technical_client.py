import json

import httpx
import pytest

from app.inventory_client import InventoryClientError
from app.technical_client import TechnicalReport, WorkerTechnicalClient
from test_inventory_client import inventory_payload, rehash
from test_material_operations import StreamingResponse


def technical_payload(identity="SAFE_0001_G03"):
    inventory = inventory_payload(identity)
    return {"schema_version": 1, "validator_version": "pbr-images-1", "inventory": inventory,
        "images": [{"path": inventory["entries"][1]["path"], "map": "COL", "width": 4096, "height": 4096,
                    "bits": 8, "format": "PNG", "sha256": inventory["entries"][1]["sha256"]}],
        "errors": [], "warnings": [], "can_approve": True}


def test_real_wire_contract_uses_shared_bounded_transport(monkeypatch):
    def stream(method, url, **kwargs):
        assert method == "POST" and url == "http://worker/internal/material-validate"
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return StreamingResponse([json.dumps(technical_payload()).encode()])
    monkeypatch.setattr(httpx, "stream", stream)
    assert WorkerTechnicalClient("http://worker").validate("library/SAFE_0001_G03").can_approve


@pytest.mark.parametrize("defect", ["hash", "missing-image", "duplicate", "dimensions", "bits", "path", "format", "unknown-master-file",
    "unknown-finding", "private-message", "contradiction", "huge", "identity", "version"])
def test_forged_successful_reports_fail_closed_without_reflection(monkeypatch, defect):
    payload = technical_payload(); image = payload["images"][0]
    if defect == "hash": image["sha256"] = "a" * 64
    elif defect == "missing-image": payload["images"] = []
    elif defect == "duplicate": payload["images"].append(image.copy())
    elif defect == "dimensions": image["width"] = 32; image["height"] = 32
    elif defect == "bits": image["bits"] = 32
    elif defect == "path": image["path"] = "../../SYNTHETIC_SECRET"
    elif defect == "format": image["format"] = "JPEG"
    elif defect == "unknown-master-file":
        payload["inventory"]["entries"].append({"path": "4K/unknown.png", "kind": "file", "size": 0, "sha256": "a" * 64})
        rehash(payload["inventory"])
    elif defect == "unknown-finding": payload["warnings"] = [{"code": "SYNTHETIC_SECRET", "path": ""}]
    elif defect == "private-message": payload["warnings"] = [{"code": "PREVIEW_MISSING", "path": "", "message": "SYNTHETIC_SECRET"}]
    elif defect == "contradiction": payload["errors"] = [{"code": "IMAGE_UNREADABLE", "path": image["path"]}]
    elif defect == "huge": image["width"] = image["height"] = 32768
    elif defect == "identity": payload = technical_payload("OTHER_0001_G03")
    else: payload["validator_version"] = "unsupported"
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: StreamingResponse([json.dumps(payload).encode()]))
    with pytest.raises(InventoryClientError) as caught:
        WorkerTechnicalClient("http://worker").validate("SAFE_0001_G03")
    assert "SYNTHETIC_SECRET" not in str(caught.value) and caught.value.__cause__ is None


def test_error_report_can_retain_partial_image_facts():
    payload = technical_payload()
    payload.update(images=[], errors=[{"code": "IMAGE_UNREADABLE", "path": payload["images"][0]["path"]}], can_approve=False)
    assert not TechnicalReport.model_validate_json(json.dumps(payload)).can_approve
