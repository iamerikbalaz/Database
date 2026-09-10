import hashlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.preflight import MAX_METADATA_BYTES
from app.secure_filesystem import (
    MaterialFolderNotFound,
    SecureFilesystemAccessUnavailable,
    secure_filesystem_access_supported,
)


BOUNDARY = datetime(2026, 3, 4, tzinfo=ZoneInfo("Europe/Prague"))
EXPECTED_TOP_LEVEL_KEYS = {
    "schema_version", "folder_path", "folder_name", "master_resolution",
    "master_last_modified_at", "policy", "metadata", "warnings", "errors",
    "can_continue",
}
EXPECTED_METADATA_KEYS = {
    "status", "source_file_name", "sha256", "raw_content", "hex_color",
    "width_cm", "height_cm", "warnings", "errors",
}
REQUIRES_SECURE_FILESYSTEM = pytest.mark.skipif(
    not secure_filesystem_access_supported(),
    reason="descriptor-relative O_NOFOLLOW access is unavailable",
)


def make_material(root: Path, relative: str = "collection/TECHNICAL_IDENTITY") -> Path:
    material = root.joinpath(*relative.split("/"))
    material.mkdir(parents=True)
    (material / "4K").mkdir()
    return material


def client_for(root: Path | str | None) -> TestClient:
    return TestClient(create_app(root))


def snapshot(root: Path) -> dict[str, tuple[int, int, bytes | None]]:
    return {
        str(path.relative_to(root)): (
            path.stat().st_mtime_ns,
            path.stat().st_ctime_ns,
            path.read_bytes() if path.is_file() else None,
        )
        for path in [root, *root.rglob("*")]
    }


def test_health_is_preserved(tmp_path):
    response = client_for(tmp_path).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "worker", "version": "0.1.0"}


@REQUIRES_SECURE_FILESYSTEM
def test_valid_relative_path_and_missing_metadata(tmp_path):
    make_material(tmp_path)
    response = client_for(tmp_path).post(
        "/internal/material-preflight",
        json={"folder_path": "collection/TECHNICAL_IDENTITY"},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["folder_path"] == "collection/TECHNICAL_IDENTITY"
    assert body["folder_name"] == "TECHNICAL_IDENTITY"
    assert body["metadata"]["status"] == "MISSING"
    assert body["metadata"]["source_file_name"] is None
    assert body["can_continue"] is True


def test_missing_folder_has_exact_contract(tmp_path, monkeypatch):
    def missing(*args, **kwargs):
        raise MaterialFolderNotFound("missing")

    monkeypatch.setattr("app.api.inspect_material_secure", missing)
    response = client_for(tmp_path).post(
        "/internal/material-preflight", json={"folder_path": "missing/material"}
    )
    assert response.status_code == 404
    body = response.json()
    assert body == {
        "schema_version": 1,
        "folder_path": "missing/material",
        "folder_name": "material",
        "master_resolution": None,
        "master_last_modified_at": None,
        "policy": None,
        "metadata": {
            "status": "MISSING", "source_file_name": None, "sha256": None,
            "raw_content": None, "hex_color": None, "width_cm": None,
            "height_cm": None, "warnings": [], "errors": [],
        },
        "warnings": [],
        "errors": [{
            "code": "MATERIAL_FOLDER_NOT_FOUND", "path": "missing/material",
            "message": "Material folder does not exist",
        }],
        "can_continue": False,
    }


@pytest.mark.parametrize("folder_path", [
    "C:\\materials\\item", "C:/materials/item", "/materials/item",
    "\\\\server\\share\\material",
])
def test_absolute_windows_and_unix_paths_are_rejected_without_access(tmp_path, folder_path):
    response = client_for(tmp_path).post(
        "/internal/material-preflight", json={"folder_path": folder_path}
    )
    assert response.status_code == 422
    assert response.json()["folder_path"] == ""
    assert response.json()["errors"][0]["code"] == "INVALID_FOLDER_PATH"


@pytest.mark.parametrize("folder_path", ["../outside", "inside/../outside", "inside\\..\\outside"])
def test_parent_traversal_is_rejected(tmp_path, folder_path):
    response = client_for(tmp_path).post(
        "/internal/material-preflight", json={"folder_path": folder_path}
    )
    assert response.status_code == 422
    assert response.json()["can_continue"] is False


@pytest.mark.parametrize("folder_path", [".", "inside/./material"])
def test_dot_component_is_rejected(tmp_path, folder_path):
    response = client_for(tmp_path).post(
        "/internal/material-preflight", json={"folder_path": folder_path}
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "INVALID_FOLDER_PATH"


def test_null_byte_is_rejected(tmp_path):
    response = client_for(tmp_path).post(
        "/internal/material-preflight", json={"folder_path": "item\u0000/child"}
    )
    assert response.status_code == 422


def test_invalid_request_keeps_versioned_response_contract(tmp_path):
    response = client_for(tmp_path).post("/internal/material-preflight", json={})
    assert response.status_code == 422
    assert set(response.json()) == EXPECTED_TOP_LEVEL_KEYS
    assert response.json()["schema_version"] == 1


@REQUIRES_SECURE_FILESYSTEM
def test_windows_junction_attribute_is_rejected(tmp_path, monkeypatch):
    import stat
    from types import SimpleNamespace

    junction = tmp_path / "junction"
    junction.mkdir()
    original_lstat = Path.lstat
    monkeypatch.setattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400, raising=False)

    def lstat(self, *args, **kwargs):
        info = original_lstat(self, *args, **kwargs)
        if self == junction:
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_file_attributes=0x400,
            )
        return info

    monkeypatch.setattr(Path, "lstat", lstat)
    response = client_for(tmp_path).post(
        "/internal/material-preflight", json={"folder_path": "junction"}
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "UNSAFE_MATERIAL_PATH"


@REQUIRES_SECURE_FILESYSTEM
def test_symlink_escape_is_rejected_when_supported(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Directory symlinks are not available: {exc}")

    response = client_for(root).post(
        "/internal/material-preflight", json={"folder_path": "escape"}
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "UNSAFE_MATERIAL_PATH"


def test_missing_materials_root(monkeypatch):
    monkeypatch.delenv("MATERIALS_ROOT", raising=False)
    response = TestClient(create_app()).post(
        "/internal/material-preflight", json={"folder_path": "collection/material"}
    )
    assert response.status_code == 503
    assert response.json() == {
        "schema_version": 1,
        "folder_path": "collection/material",
        "folder_name": "material",
        "master_resolution": None,
        "master_last_modified_at": None,
        "policy": None,
        "metadata": {
            "status": "INVALID", "source_file_name": None, "sha256": None,
            "raw_content": None, "hex_color": None, "width_cm": None,
            "height_cm": None, "warnings": [], "errors": [],
        },
        "warnings": [],
        "errors": [{
            "code": "MATERIALS_ROOT_UNAVAILABLE", "path": "collection/material",
            "message": "MATERIALS_ROOT is not configured or unavailable",
        }],
        "can_continue": False,
    }


def test_unsupported_secure_filesystem_returns_fail_closed_error(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise SecureFilesystemAccessUnavailable("unsupported")

    monkeypatch.setattr("app.api.inspect_material_secure", unavailable)
    response = client_for(tmp_path).post(
        "/internal/material-preflight", json={"folder_path": "collection/material"}
    )
    body = response.json()
    assert response.status_code == 422
    assert body["errors"] == [{
        "code": "SECURE_FILESYSTEM_ACCESS_UNAVAILABLE",
        "path": "collection/material",
        "message": "Secure descriptor-based filesystem access is unavailable",
    }]
    assert body["can_continue"] is False
    assert str(tmp_path) not in response.text


@REQUIRES_SECURE_FILESYSTEM
def test_valid_metadata_txt(tmp_path):
    material = make_material(tmp_path)
    raw = json.dumps({
        "COLOR": {"hex": "a1b2c3"},
        "TEXTURE_SIZE": {"cm": {"width": 12.50, "height": 34}},
    }, separators=(",", ":")).encode()
    (material / "metadata.txt").write_bytes(raw)

    response = client_for(tmp_path).post(
        "/internal/material-preflight",
        json={"folder_path": "collection/TECHNICAL_IDENTITY"},
    )
    metadata = response.json()["metadata"]
    assert response.status_code == 200
    assert metadata == {
        "status": "VALID",
        "source_file_name": "metadata.txt",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "raw_content": raw.decode(),
        "hex_color": "#A1B2C3",
        "width_cm": "12.5",
        "height_cm": "34",
        "warnings": [],
        "errors": [],
    }


@REQUIRES_SECURE_FILESYSTEM
def test_invalid_metadata_is_nonblocking(tmp_path):
    material = make_material(tmp_path)
    raw = b"{broken"
    (material / "metadata.txt").write_bytes(raw)
    response = client_for(tmp_path).post(
        "/internal/material-preflight",
        json={"folder_path": "collection/TECHNICAL_IDENTITY"},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["metadata"]["status"] == "INVALID"
    assert body["metadata"]["raw_content"] == raw.decode()
    assert body["metadata"]["errors"]
    assert body["can_continue"] is True


@REQUIRES_SECURE_FILESYSTEM
@pytest.mark.parametrize("delta,expected", [
    (-1, "LEGACY_BEFORE_2026_03_04"),
    (0, "CURRENT_ON_OR_AFTER_2026_03_04"),
])
def test_16k_master_and_policy(tmp_path, delta, expected):
    material = make_material(tmp_path)
    (material / "16K").mkdir()
    ns = (int(BOUNDARY.timestamp()) + delta) * 1_000_000_000
    os.utime(material / "16K", ns=(ns, ns))
    response = client_for(tmp_path).post(
        "/internal/material-preflight",
        json={"folder_path": "collection/TECHNICAL_IDENTITY"},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["master_resolution"] == "16K"
    assert body["policy"] == expected
    assert body["master_last_modified_at"] is not None


@REQUIRES_SECURE_FILESYSTEM
def test_metadata_size_limit_is_structured_and_bounded(tmp_path, monkeypatch):
    material = make_material(tmp_path)
    metadata_path = material / "metadata.txt"
    metadata_path.write_bytes(b"x" * (MAX_METADATA_BYTES + 1))
    original_open = Path.open

    def should_not_open(self, *args, **kwargs):
        if self == metadata_path:
            pytest.fail("Oversized metadata must be rejected before it is opened")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", should_not_open)
    response = client_for(tmp_path).post(
        "/internal/material-preflight",
        json={"folder_path": "collection/TECHNICAL_IDENTITY"},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["metadata"]["status"] == "INVALID"
    assert body["metadata"]["sha256"] is None
    assert body["metadata"]["raw_content"] is None
    assert {item["code"] for item in body["metadata"]["errors"]} == {
        "SOURCE_METADATA_TOO_LARGE"
    }
    assert body["can_continue"] is True


@REQUIRES_SECURE_FILESYSTEM
def test_request_does_not_change_source_files(tmp_path):
    material = make_material(tmp_path)
    (material / "metadata.txt").write_text("texture size: 12x34 cm", encoding="utf-8")
    (material / "4K" / "map.jpg").write_bytes(b"synthetic")
    before = snapshot(tmp_path)
    response = client_for(tmp_path).post(
        "/internal/material-preflight",
        json={"folder_path": "collection/TECHNICAL_IDENTITY"},
    )
    assert response.status_code == 200
    assert snapshot(tmp_path) == before


@REQUIRES_SECURE_FILESYSTEM
def test_raw_content_is_not_logged(tmp_path, caplog):
    material = make_material(tmp_path)
    secret = "texture size: 12x34 cm SECRET-METADATA-VALUE"
    (material / "metadata.txt").write_text(secret, encoding="utf-8")
    caplog.set_level(logging.DEBUG)
    response = client_for(tmp_path).post(
        "/internal/material-preflight",
        json={"folder_path": "collection/TECHNICAL_IDENTITY"},
    )
    assert response.status_code == 200
    assert secret not in caplog.text


@REQUIRES_SECURE_FILESYSTEM
def test_stable_json_contract(tmp_path):
    material = make_material(tmp_path)
    (material / "metadata.txt").write_text("texture size: 12x34 cm", encoding="utf-8")
    response = client_for(tmp_path).post(
        "/internal/material-preflight",
        json={"folder_path": "collection/TECHNICAL_IDENTITY"},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["schema_version"] == 1
    assert set(body) == EXPECTED_TOP_LEVEL_KEYS
    assert set(body["metadata"]) == EXPECTED_METADATA_KEYS
    assert all(set(item) == {"code", "path", "message"}
               for item in [*body["warnings"], *body["errors"],
                            *body["metadata"]["warnings"], *body["metadata"]["errors"]])
    serialized = response.text
    assert str(tmp_path) not in serialized
    assert str(tmp_path).replace("\\", "/") not in serialized
