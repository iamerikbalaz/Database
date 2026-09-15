import hashlib
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.api import create_app
from app.identity_plan import IdentityPlanError, IdentityTarget, plan_identity_change, rewrite_metadata
from app.secure_filesystem import secure_filesystem_access_supported

OLD = "SAFE_0001_G03"
NEW = "OTHER_0012_G04"
TARGET = IdentityTarget("new-brand/" + NEW, "New brand", "New product")
POSIX = pytest.mark.skipif(not secure_filesystem_access_supported(), reason="Linux descriptor-relative rename planning")


def source(tmp_path):
    material = tmp_path / "old-brand" / OLD
    (material / "4K").mkdir(parents=True)
    (material / "SOURCE").mkdir()
    (tmp_path / "new-brand").mkdir()
    (material / "4K" / (OLD + "_COL_4K.png")).write_bytes(b"synthetic-map")
    (material / "SOURCE" / (OLD + ".sbs")).write_bytes(b"synthetic-source")
    return material


def metadata():
    return {"FOLDER": OLD, "BASE_NAME": OLD, "MANUFACTURER": "Old brand", "PRODUCT_NUMBER": "0001",
        "PRODUCT_NAME": "Old product", "CATEGORY": "G03", "TEXTURE_SIZE_SOURCE": f"4K/{OLD}_COL_4K.png",
        "COLOR": {"hex": "#AABBCC", "measured_from": f"4K/{OLD}_COL_4K.png"},
        "SOURCE": {"SBS": f"SOURCE/{OLD}.sbs"}, "TEXTURE_SIZE": {"cm": {"width": 12.5, "height": 34}}}


def test_pure_metadata_rewrite_updates_only_documented_identity_fields():
    original = metadata()
    rewritten, fields = rewrite_metadata(json.dumps(original).encode(), OLD, TARGET)
    data = json.loads(rewritten)
    assert data["FOLDER"] == NEW and data["BASE_NAME"] == NEW
    assert data["PRODUCT_NUMBER"] == "0012" and data["CATEGORY"] == "G04"
    assert data["MANUFACTURER"] == TARGET.brand_name and data["PRODUCT_NAME"] == TARGET.material_name
    assert data["SOURCE"]["SBS"] == f"SOURCE/{NEW}.sbs"
    assert data["COLOR"]["measured_from"] == f"4K/{NEW}_COL_4K.png"
    assert data["COLOR"]["hex"] == original["COLOR"]["hex"] and data["TEXTURE_SIZE"] == original["TEXTURE_SIZE"]
    assert len(fields) == 9


def test_metadata_rewrite_keeps_exact_number_tokens_and_numeric_product_number():
    raw = ('{"FOLDER":"' + OLD + '","PRODUCT_NUMBER":1,"exact":123456789.123456789123456789,"small":1e-10000}').encode()
    rewritten, _ = rewrite_metadata(raw, OLD, TARGET)
    assert b'"exact":123456789.123456789123456789' in rewritten and b'"small":1e-10000' in rewritten
    assert b'"PRODUCT_NUMBER":12' in rewritten


def test_observed_dimensions_text_is_kept_byte_exact():
    raw = b"texture size: 12.5x34 cm\n"
    assert rewrite_metadata(raw, OLD, TARGET) == (raw, [])


@pytest.mark.parametrize("defect", ["invalid", "duplicate", "web", "wrong-folder", "unmapped-reference", "wrong-number", "nested-depth"])
def test_ambiguous_metadata_cannot_be_silently_rewritten(defect):
    data = metadata()
    if defect == "wrong-folder": data["FOLDER"] = "UNRELATED_0001_G03"
    elif defect == "unmapped-reference": data["private_reference"] = "prefix-" + OLD
    elif defect == "wrong-number": data["PRODUCT_NUMBER"] = "0999"
    raw = json.dumps(data).encode()
    if defect == "invalid": raw = b"SYNTHETIC_PRIVATE_UNSUPPORTED"
    elif defect == "duplicate": raw = b'{"FOLDER":"a","FOLDER":"b"}'
    elif defect == "web": raw = b'{"WEB_APP_PART":{}}'
    elif defect == "nested-depth": raw = b'{"nested":' + b'[' * 66 + b'1' + b']' * 66 + b'}'
    with pytest.raises(IdentityPlanError) as caught: rewrite_metadata(raw, OLD, TARGET)
    assert "PRIVATE" not in str(caught.value)


@POSIX
def test_complete_plan_is_read_only_and_bound_to_exact_source(tmp_path):
    material = source(tmp_path)
    (material / "metadata.txt").write_text(json.dumps(metadata()), encoding="utf-8")
    before = {str(path): (path.stat().st_mtime_ns, path.read_bytes()) for path in material.rglob("*") if path.is_file()}
    plan = plan_identity_change(tmp_path, ("old-brand", OLD), TARGET)
    assert plan["ready"] and plan["source_path"] == "old-brand/" + OLD and plan["target_path"] == TARGET.path
    assert len(plan["changes"]) == 2 and len(plan["metadata"]["changed_fields"]) == 9
    assert plan["metadata"]["before_hash"] == hashlib.sha256((material / "metadata.txt").read_bytes()).hexdigest()
    assert before == {str(path): (path.stat().st_mtime_ns, path.read_bytes()) for path in material.rglob("*") if path.is_file()}
    assert not (tmp_path / TARGET.path).exists()
    assert str(tmp_path) not in json.dumps(plan) and "Old product" not in json.dumps(plan)
    assert plan_identity_change(tmp_path, ("old-brand", OLD), TARGET) == plan
    (material / "4K" / (OLD + "_COL_4K.png")).write_bytes(b"changed")
    assert plan_identity_change(tmp_path, ("old-brand", OLD), TARGET)["plan_hash"] != plan["plan_hash"]


@POSIX
@pytest.mark.parametrize("collision", ["root", "root-case", "file", "file-case"])
def test_all_target_collisions_block_the_plan(tmp_path, collision):
    material = source(tmp_path)
    if collision == "root": (tmp_path / TARGET.path).mkdir()
    elif collision == "root-case": (tmp_path / "new-brand" / NEW.lower()).mkdir()
    else: (material / "4K" / ((NEW if collision == "file" else NEW.lower()) + "_COL_4K.png")).write_bytes(b"must not overwrite")
    plan = plan_identity_change(tmp_path, ("old-brand", OLD), TARGET)
    assert plan["ready"] is False and "IDENTITY_TARGET_COLLISION" in {item["code"] for item in plan["errors"]}


@POSIX
def test_missing_metadata_is_visible_but_does_not_block_planning(tmp_path):
    source(tmp_path)
    plan = plan_identity_change(tmp_path, ("old-brand", OLD), TARGET)
    assert plan["ready"] is True and plan["metadata"]["after_hash"] is None
    assert plan["warnings"] == [{"code": "SOURCE_METADATA_MISSING", "path": "metadata.txt"}]


@POSIX
def test_target_parent_links_are_rejected_without_outside_access(tmp_path):
    source(tmp_path)
    (tmp_path / "linked").symlink_to(tmp_path / "new-brand", target_is_directory=True)
    response = TestClient(create_app(tmp_path)).post("/internal/material-identity-plan", json={
        "folder_path": "old-brand/" + OLD, "target_path": "linked/" + NEW,
        "brand_name": TARGET.brand_name, "material_name": TARGET.material_name})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "UNSAFE_MATERIAL_PATH"


@POSIX
def test_api_never_returns_source_metadata_contents(tmp_path):
    material = source(tmp_path)
    value = metadata(); value["private_note"] = "SYNTHETIC_PRIVATE_METADATA"
    (material / "metadata.txt").write_text(json.dumps(value), encoding="utf-8")
    response = TestClient(create_app(tmp_path)).post("/internal/material-identity-plan", json={
        "folder_path": "old-brand/" + OLD, "target_path": TARGET.path,
        "brand_name": TARGET.brand_name, "material_name": TARGET.material_name})
    assert response.status_code == 200 and response.json()["ready"] is True
    assert "SYNTHETIC_PRIVATE_METADATA" not in response.text and str(tmp_path) not in response.text


@pytest.mark.parametrize("target", ["../" + NEW, "/" + NEW, "C:/" + NEW, "new-brand/OTHER_0000_G04", "new-brand/bad-name"])
def test_bad_target_is_rejected_before_inventory(tmp_path, monkeypatch, target):
    monkeypatch.setattr("app.identity_plan.inventory_material", lambda *_: pytest.fail("must validate first"))
    with pytest.raises(IdentityPlanError, match="IDENTITY_TARGET_INVALID"):
        plan_identity_change(tmp_path, ("old-brand", OLD), IdentityTarget(target, "Brand", "Product"))
