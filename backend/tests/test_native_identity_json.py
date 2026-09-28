from decimal import Decimal
import hashlib
import json
from types import SimpleNamespace

import pytest

from app.local_materials import LocalIdentity, source
from app.metadata_identity_document import MetadataIdentityError, rewrite_identity_metadata

OLD = "SWISSPEARL_0001_OLD-NAME_G03"
NEW = "SWISSPEARL_0001_NEW-NAME_G03"


def document():
    return ('{"FOLDER":"' + OLD + '","MANUFACTURER":"SWISSPEARL","PRODUCT_NUMBER":"0001",'
        '"PRODUCT_NAME":"OLD NAME","CATEGORY":"G03","BASE_NAME":"SWISSPEARL_0001_OLD-NAME",'
        '"COLOR":{"hex":"#999999","delta_e":5.86,"method":"chromatic","measured_from":"SOURCE\\\\SWISSPEARL_0001_OLD-NAME_COL_4K.jpg"},'
        '"TEXTURE_SIZE":{"cm":{"width":12.9571234567890123456789,"height":13.845},"in":{"width":5.1011},"method":"sbs_crop_matrix_inverse"},'
        '"TEXTURE_SIZE_SOURCE":"' + OLD + '.sbs","SOURCE":{"SBS":"SOURCE/' + OLD + '.sbs"},"unknown":1.230000e-42}').encode()


def rewrite(raw):
    return rewrite_identity_metadata(raw, OLD, new_identity=NEW, brand_name="SWISSPEARL", material_name="NEW NAME")


def test_identity_only_rewrite_preserves_exact_numbers_provenance_and_path_separators():
    raw = document(); result, fields = rewrite(raw)
    before = json.loads(raw, parse_float=Decimal); after = json.loads(result, parse_float=Decimal)
    assert after["TEXTURE_SIZE"] == before["TEXTURE_SIZE"]
    assert after["COLOR"]["delta_e"] == before["COLOR"]["delta_e"]
    assert after["COLOR"]["method"] == "chromatic"
    assert b"12.9571234567890123456789" in result and b"1.230000e-42" in result
    assert after["COLOR"]["measured_from"] == "SOURCE\\SWISSPEARL_0001_NEW-NAME_COL_4K.jpg"
    assert after["SOURCE"]["SBS"] == "SOURCE/" + NEW + ".sbs"
    assert "COLOR.measured_from" in fields and "FOLDER" in fields
    assert OLD not in result.decode()


def test_generated_complete_schema_survives_rename_and_known_inventory_references_follow_it():
    from app.metadata_document import rewrite_metadata_json
    from app.identity_client import IdentityMetadata
    identity = {"FOLDER": OLD, "MANUFACTURER": "SWISSPEARL", "PRODUCT_NUMBER": "0001",
        "PRODUCT_NAME": "OLD NAME", "CATEGORY": "G03", "BASE_NAME": OLD.rsplit("_", 1)[0]}
    raw = rewrite_metadata_json(None, {"hex_color": "#999999", "width_cm": "12.957", "height_cm": "13.845"}, identity,
        inventory=[{"path": "4K", "kind": "directory"}, {"path": f"4K/{OLD}_notes.txt", "kind": "file"},
            {"path": "2K", "kind": "directory"}, {"path": f"2K/{OLD}_readme.txt", "kind": "file"}])
    result, fields = rewrite(raw)
    before, after = json.loads(raw), json.loads(result)
    assert result.startswith(b'{\n    "FOLDER":')
    assert b'\n        "runner_up": {\n            "hex": null,' in result
    assert list(after) == list(before)
    assert set(after) == set(before)
    assert after["COLOR"] == before["COLOR"] and after["TEXTURE_SIZE"] == before["TEXTURE_SIZE"]
    assert after["RESOLUTIONS"]["4K"]["UNRECOGNIZED"] == [NEW + "_notes.txt"]
    assert after["RESOLUTIONS"]["2K"]["UNRECOGNIZED"] == [NEW + "_readme.txt"]
    assert fields.count("RESOLUTIONS.UNRECOGNIZED") == 1
    IdentityMetadata(before_hash=hashlib.sha256(raw).hexdigest(), after_hash=hashlib.sha256(result).hexdigest(), changed_fields=fields)


@pytest.mark.parametrize("new_name", ["OLD-NAME-V51-TEST", "OLD", "OLD-NAME.REVISED"])
def test_name_extension_and_shortening_preserve_only_explicitly_rewritten_references(new_name):
    target = f"SWISSPEARL_0001_{new_name}_G03"
    data = json.loads(document())
    data["SOURCE"]["SBS"] = "SOURCE/SWISSPEARL_0001_OLD-NAME_B01.sbs"
    data["RESOLUTIONS"] = {"4K": {"UNRECOGNIZED": ["SWISSPEARL_0001_OLD-NAME_notes.txt"]}}
    def apply(value):
        return rewrite_identity_metadata(json.dumps(value).encode(), OLD, new_identity=target,
            brand_name="SWISSPEARL", material_name=new_name)
    result, _ = apply(data)
    after = json.loads(result)
    assert after["FOLDER"] == target and after["BASE_NAME"] == target.rsplit("_", 1)[0]
    assert after["SOURCE"]["SBS"] == f"SOURCE/SWISSPEARL_0001_{new_name}_B01.sbs"
    assert after["RESOLUTIONS"]["4K"]["UNRECOGNIZED"] == [f"SWISSPEARL_0001_{new_name}_notes.txt"]
    assert after["TEXTURE_SIZE"] == data["TEXTURE_SIZE"]
    # Unknown references must not be globally masked just because the target
    # contains the old name, nor accidentally hidden by a shorter target.
    with pytest.raises(MetadataIdentityError, match="METADATA_UNMAPPED_REFERENCE"):
        apply({**data, "unknown": ["SWISSPEARL_0001_OLD-NAME_COL_4K.jpg"]})
    for reference in ("prefix-SWISSPEARL_0001_OLD-NAME.sbs", OLD + "_" + OLD + ".sbs"):
        with pytest.raises(MetadataIdentityError, match="METADATA_UNMAPPED_REFERENCE"):
            apply({**data, "SOURCE": {"SBS": reference}})


@pytest.mark.parametrize("value", [{"unknown": OLD}, {"unknown": ["SWISSPEARL_0001_OLD-NAME_COL_4K.png"]}])
def test_unknown_old_references_block_plan_instead_of_leaving_stale_paths(value):
    raw = json.dumps({"FOLDER": OLD, **value}).encode()
    with pytest.raises(MetadataIdentityError, match="METADATA_UNMAPPED_REFERENCE"):
        rewrite(raw)


def test_native_plan_uses_identity_only_transform_and_exposes_fixed_metadata_errors(tmp_path):
    raw = document()
    fs = SimpleNamespace(path=lambda relative: tmp_path / relative, read_handle=lambda handle, limit: handle)
    client = LocalIdentity(SimpleNamespace(fs=fs))
    request = {"folder_path": "SWISSPEARL/" + OLD, "target_path": "SWISSPEARL/" + NEW,
        "brand_name": "SWISSPEARL", "material_name": "NEW NAME"}
    def plan(data):
        return client._plan(request, [{"path": "metadata.json", "kind": "file", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}], {"metadata.json": data})
    result, rewritten = plan(raw)
    assert result.ready
    result.verify_request(request)
    assert b"12.9571234567890123456789" in rewritten
    blocked, rewritten = plan(json.dumps({"unknown": OLD}).encode())
    assert not blocked.ready and rewritten is None
    assert blocked.errors[0].code == "METADATA_UNMAPPED_REFERENCE"
    assert not blocked.warnings


@pytest.mark.parametrize("raw", [b'{"COLOR":{"hex":"#AABBCC"},"COLOR":{}}', b'{"unknown":NaN}',
    b'{"TEXTURE_SIZE":{"cm":{"width":true,"height":20}}}', b'{"TEXTURE_SIZE":{"cm":{"width":"10.2","height":20}}}',
    b'{"WEB_APP_PART":{},"DESKTOP_APP_PART":{}}'])
def test_native_source_rejects_duplicate_nonfinite_boolean_and_manifest_json(raw):
    result = source(raw, OLD)
    assert result.status == "INVALID" and result.hex_color is None
    assert result.raw_content == raw.decode()


def test_native_source_preserves_supported_precision():
    raw = b'{"COLOR":{"hex":"#ABCDEF"},"TEXTURE_SIZE":{"cm":{"width":12.957,"height":13.845}}}'
    result = source(raw, OLD)
    assert result.status == "VALID" and result.width_cm == "12.957" and result.height_cm == "13.845"
