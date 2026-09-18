"""Standalone smoke for the production packaging image; stdlib + runtime deps only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4

from PIL import Image
from app.packaging_plan import _digest
from app.technical_validation import validate_material

BASE = "http://127.0.0.1:8081"
IDENTITY = "SYNTHETIC_SMOKE_0001_G03"


def main(export_path=None, variant="single"):
    assert variant in {"single", "multi-current", "nonstandard", "square"}
    root = Path("/tmp") / ("packaging-smoke-" + uuid4().hex); root.mkdir(mode=0o700)
    source, workspace, artifacts, journal = [root / name for name in ("materials", "workspace", "artifacts", "journal")]
    for path in (source, workspace, artifacts, journal): path.mkdir(mode=0o700)
    master = "4K" if variant == "nonstandard" else "2K" if variant == "multi-current" else "1K"
    size = (3072, 1105) if variant == "nonstandard" else (2048, 736) if variant == "multi-current" else (1024, 1024) if variant == "square" else (1024, 368)
    folder = source / IDENTITY; (folder / master).mkdir(parents=True)
    Image.linear_gradient("L").resize(size).convert("RGB").save(folder / master / (IDENTITY + "_COL_" + master + ".png"))
    if variant != "single":
        Image.new("I;16", size, 54321).save(folder / master / (IDENTITY + "_NRM16_" + master + ".png"))
        (folder / "metadata.txt").write_bytes(b'{"WEB_APP_PART":{},"DESKTOP_APP_PART":{}}\r\n')
    preview = folder / "PREVIEW"; preview.mkdir()
    (preview / "preview.png").write_bytes(b"Synthetic preview preserved")
    if variant != "single":
        (preview / "empty").mkdir()
        (preview / "český náhled.png").write_bytes(b"Synthetic unicode preview")
    report = validate_material(source, (IDENTITY,)); assert report["can_approve"]
    original = {str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest() for path in folder.rglob("*") if path.is_file()}
    token = uuid4().hex + uuid4().hex
    environment = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "PACKAGING_ENABLED": "true", "PACKAGING_SERVICE_TOKEN": token,
        "MATERIALS_ROOT": str(source), "PACKAGING_WORKSPACE_ROOT": str(workspace), "PACKAGING_ARTIFACT_ROOT": str(artifacts), "PACKAGING_JOURNAL_ROOT": str(journal)}
    def call(path, body=None, *, authorized=True):
        headers = {"Content-Type": "application/json"}
        if authorized: headers["Authorization"] = "Bearer " + token
        data = None if body is None else json.dumps(body, allow_nan=False).encode()
        request = urllib.request.Request(BASE + path, data=data, headers=headers)
        try: response = urllib.request.urlopen(request, timeout=120)
        except urllib.error.HTTPError as error: response = error
        with response:
            raw = response.read(32 * 1024**2 + 1)
            assert len(raw) <= 32 * 1024**2 and token.encode() not in raw
            return response.status, json.loads(raw)
    def start():
        child = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.packaging_api:app", "--host", "127.0.0.1", "--port", "8081",
            "--workers", "1", "--no-access-log", "--no-proxy-headers", "--limit-concurrency", "8", "--timeout-keep-alive", "5"],
            cwd="/app", env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 15
        try:
            while True:
                assert child.poll() is None, "Packaging service exited"
                try:
                    status, body = call("/health")
                    if status == 200 and body == {"service": "packaging", "status": "ready"}: return child
                except (OSError, urllib.error.URLError): pass
                assert time.monotonic() < deadline, "Packaging service did not become ready"
                time.sleep(.05)
        except BaseException:
            stop(child)
            raise
    def stop(child):
        child.terminate()
        try: child.wait(timeout=10)
        except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)
    child = start()
    try:
        status, body = call("/internal/packaging/execute", {"synthetic": "private"}, authorized=False)
        assert status == 401 and body["detail"]["code"] == "PACKAGING_SERVICE_UNAUTHORIZED"
        status, prepared = call("/internal/packaging/prepare", {
            "operation_id": str(uuid4()), "parts": [IDENTITY], "expected_source_revision_hash": report["inventory"]["source_revision_hash"],
            "expected_technical_report_hash": _digest({key: value for key, value in report.items() if key != "inventory"}),
            "approval_context_hash": "a" * 64, "policy": "CURRENT_ON_OR_AFTER_2026_03_04" if variant == "multi-current" else "LEGACY_BEFORE_2026_03_04",
            "storage_timezone": "Europe/Prague" if variant == "multi-current" else "UTC", "report": report})
        assert status == 200 and _digest(prepared["request"]) == prepared["request_hash"]
        status, completed = call("/internal/packaging/execute", {**prepared, "report": report})
        assert status == 200 and completed["status"] == "READY" and completed["attempt"] == 1
        assert _digest(completed["stored"]["payload"]) == completed["stored"]["proof_sha256"]
        assert not list(workspace.iterdir())
        assert {str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest() for path in folder.rglob("*") if path.is_file()} == original
        source.rename(source.with_name("offline-materials"))
    finally: stop(child)
    child = start()
    try:
        status, recovered = call("/internal/packaging/reconcile", prepared)
        assert status == 200 and recovered == completed
        status, replayed = call("/internal/packaging/execute", {**prepared, "report": report, "retry": True})
        assert status == 200 and replayed == completed and not list(workspace.iterdir())
    finally: stop(child)
    if export_path is not None:
        Path(export_path).write_text(json.dumps({"report": report, "prepared": prepared, "result": completed},
            ensure_ascii=True, sort_keys=True, indent=2) + "\n")
    print("Packaging production image smoke: actual HTTP, conversion, restart and offline replay passed.")


if __name__ == "__main__": main(sys.argv[1] if len(sys.argv) >= 2 else None, sys.argv[2] if len(sys.argv) >= 3 else "single")
