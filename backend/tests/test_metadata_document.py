from decimal import Decimal
import json
from pathlib import Path

import pytest

from app.metadata_client import MetadataValues
from app.metadata_document import metadata_inventory_facts, rewrite_metadata_json


IDENTITY = {"FOLDER": "BRAND_0042_A-SAMPLE_G03", "MANUFACTURER": "Brand", "PRODUCT_NUMBER": "0042",
    "PRODUCT_NAME": "A sample", "CATEGORY": "G03", "BASE_NAME": "BRAND_0042_A-SAMPLE"}


def values(**changes):
    return MetadataValues(**{"hex_color": "#ABCDEF", "width_cm": "12.957", "height_cm": "13.845", **changes}).model_dump()


def test_bootstrap_uses_real_identity_and_has_no_invented_measurements():
    document = json.loads(rewrite_metadata_json(None, values(), IDENTITY))
    assert {key: document[key] for key in IDENTITY} == IDENTITY
    assert document["COLOR"] == {"hex": "#ABCDEF", "method": "database", "delta_e": None,
        "runner_up": {"hex": None, "delta_e": None}, "average_hex": None, "average_rgb": [],
        "measured_from": None, "alternatives": dict.fromkeys(("mean", "median", "dominant", "chromatic"))}
    assert document["TEXTURE_SIZE"]["cm"] == {"width": 12.957, "height": 13.845}
    assert document["TEXTURE_SIZE"]["in"] == {"width": 5.1012, "height": 5.4508}
    assert document["MAPS_SHORTCUTS"] == [] and document["RESOLUTIONS"] == {}
    assert document["SOURCE"] == {"CROPS": [], "SBS": None, "REFERENCES": None, "ARCHIVE": None}
    assert document["TEXTURE_SIZE_SOURCE"] is None and document["WARNINGS"] == []


def test_unchanged_inputs_preserve_original_precision_and_provenance():
    raw = b'{"COLOR":{"hex":"#ABCDEF","method":"chromatic","delta_e":5.86},"TEXTURE_SIZE":{"cm":{"width":12.957,"height":13.845},"in":{"width":5.1011,"height":5.4507},"method":"sbs_crop_matrix_inverse"},"unknown":0.123456789012345678901234567890}'
    result = rewrite_metadata_json(raw, values(), IDENTITY)
    parsed = json.loads(result, parse_float=Decimal)
    assert parsed["TEXTURE_SIZE"]["method"] == "sbs_crop_matrix_inverse"
    assert parsed["TEXTURE_SIZE"]["in"]["width"] == Decimal("5.1011")
    assert parsed["COLOR"]["delta_e"] == Decimal("5.86")
    assert b"0.123456789012345678901234567890" in result


def test_palette_case_normalization_does_not_discard_measurements():
    raw = b'{"COLOR":{"hex":"abcdef","delta_e":5.86,"method":"chromatic"}}'
    document = json.loads(rewrite_metadata_json(raw, values()))
    assert document["COLOR"]["hex"] == "#ABCDEF"
    assert document["COLOR"]["delta_e"] == 5.86 and document["COLOR"]["method"] == "chromatic"


def test_manual_edit_updates_derived_inches_and_removes_stale_selection_distances():
    raw = b'{"COLOR":{"hex":"#999999","delta_e":5.86,"runner_up":{"hex":"#CCCC99"},"average_rgb":[171,171,167],"measured_from":"map.jpg","method":"chromatic"},"TEXTURE_SIZE":{"cm":{"width":12.957,"height":13.845},"in":{"width":5.1011,"height":5.4507},"method":"sbs"}}'
    document = json.loads(rewrite_metadata_json(raw, values(width_cm="10.2", height_cm=None), IDENTITY))
    assert document["TEXTURE_SIZE"]["in"] == {"width": 4.0157, "height": None}
    assert document["TEXTURE_SIZE"]["cm"] == {"width": 10.2, "height": None}
    assert document["TEXTURE_SIZE"]["method"] == "manual"
    assert document["COLOR"]["average_rgb"] == [171, 171, 167]
    assert document["COLOR"]["measured_from"] == "map.jpg"
    assert document["COLOR"]["delta_e"] is None
    assert document["COLOR"]["runner_up"] == {"hex": None, "delta_e": None}


def inventory():
    base = IDENTITY["BASE_NAME"]
    return [{"path": path, "kind": kind} for path, kind in [
        ("4K", "directory"), ("2K", "directory"), ("SOURCE", "directory"),
        ("SOURCE/CROPS", "directory"), ("SOURCE/REFERENCES", "directory"),
        (f"4K/{base}_COL_4K.jpg", "file"), (f"4K/{base}_METAL_4K.png", "file"),
        (f"2K/{base}_NRM16_2K.tif", "file"), ("4K/not-a-map.png", "file"),
        (f"SOURCE/{IDENTITY['FOLDER']}.sbs", "file"), ("SOURCE/CROPS/col.png", "file"),
        ("SOURCE/thing_disp_crop.png", "file"), ("SOURCE/unrelated_nrm.png", "file")]]


def assert_template_shape(document, template):
    assert set(document) == set(template)
    for key, value in template.items():
        if key == "RESOLUTIONS":
            for resolution in document[key].values():
                assert_template_shape(resolution, template[key]["4K"])
        elif isinstance(value, dict): assert_template_shape(document[key], value)
        elif isinstance(value, list): assert isinstance(document[key], list)


def test_generation_matches_every_template_key_and_derives_only_actual_inventory_facts():
    template = json.loads((Path(__file__).parent / "fixtures/production_metadata_template.json").read_bytes())
    document = json.loads(rewrite_metadata_json(None, values(), IDENTITY, inventory=inventory()))
    assert_template_shape(document, template)
    assert document["MAPS_SHORTCUTS"] == ["COL", "METAL", "NRM16"]
    assert document["RESOLUTIONS"]["4K"] == {"MAPS_SHORTCUTS": ["COL", "METAL"], "FILE_COUNT": 3, "UNRECOGNIZED": ["not-a-map.png"]}
    assert document["SOURCE"] == {"CROPS": ["col", "disp"], "SBS": f"SOURCE/{IDENTITY['FOLDER']}.sbs", "REFERENCES": True, "ARCHIVE": False}
    assert document["TEXTURE_SIZE_SOURCE"] is None and document["COLOR"]["measured_from"] is None
    assert "SWISSPEARL" not in json.dumps(document)


def test_full_original_template_noop_preserves_all_values_and_unknown_proof():
    template = json.loads((Path(__file__).parent / "fixtures/production_metadata_template.json").read_bytes(), parse_float=Decimal)
    template["COLOR"]["runner_up"]["custom_precision"] = Decimal("0.12345678901234567890123456789")
    from app.metadata_document import _serialize
    raw = _serialize(template).encode()
    result = rewrite_metadata_json(raw, values(hex_color="#999999"), inventory=[])
    assert json.loads(result, parse_float=Decimal) == template
    assert b"0.12345678901234567890123456789" in result


def test_clearing_editable_values_keeps_complete_shape_and_unknown_nested_values():
    template = json.loads((Path(__file__).parent / "fixtures/production_metadata_template.json").read_bytes())
    template["COLOR"]["runner_up"]["custom"] = "preserved"
    document = json.loads(rewrite_metadata_json(json.dumps(template).encode(), values(hex_color=None, width_cm=None, height_cm=None)))
    assert document["COLOR"]["hex"] is None and document["COLOR"]["delta_e"] is None
    assert document["COLOR"]["runner_up"] == {"hex": None, "delta_e": None, "custom": "preserved"}
    assert document["COLOR"]["alternatives"] == template["COLOR"]["alternatives"]
    assert document["TEXTURE_SIZE"]["cm"] == {"width": None, "height": None}
    assert document["TEXTURE_SIZE"]["in"] == {"width": None, "height": None}


@pytest.mark.parametrize("entries", [[{"path": "../escape", "kind": "file"}],
    [{"path": "4K/file.png", "kind": "file"}], [{"path": "same", "kind": "file"}] * 2,
    [{"path": "C:/file", "kind": "file"}], [{"path": "link", "kind": "symlink"}]])
def test_inventory_hook_rejects_unbounded_or_inconsistent_path_facts(entries):
    with pytest.raises(ValueError, match="METADATA_INVENTORY_INVALID"):
        metadata_inventory_facts(entries, IDENTITY)


def test_empty_inventory_proves_absence_but_does_not_invent_measurements():
    document = json.loads(rewrite_metadata_json(None, values(hex_color=None, width_cm=None, height_cm=None), IDENTITY, inventory=[]))
    assert document["SOURCE"] == {"CROPS": [], "SBS": None, "REFERENCES": False, "ARCHIVE": False}
    assert document["COLOR"]["method"] is None and document["TEXTURE_SIZE"]["method"] is None


def test_partial_document_gets_inches_without_changing_existing_measurement_method():
    raw = b'{"unknown":0.123456789012345678901,"TEXTURE_SIZE":{"cm":{"width":12.957,"height":13.845},"method":"measured"}}'
    result = rewrite_metadata_json(raw, values(), IDENTITY)
    document = json.loads(result)
    assert document["TEXTURE_SIZE"]["in"] == {"width": 5.1012, "height": 5.4508}
    assert document["TEXTURE_SIZE"]["method"] == "measured"
    assert list(document) == [*IDENTITY, "TEXTURE_SIZE", "TEXTURE_SIZE_SOURCE", "COLOR", "MAPS_SHORTCUTS", "RESOLUTIONS", "SOURCE", "WARNINGS", "unknown"]
    assert result.startswith(b'{\n    "FOLDER":')
    assert b'\n        "runner_up": {\n            "hex": null,' in result
    assert b"0.123456789012345678901" in result


def test_unchanged_dimensions_keep_original_decimal_scale():
    result = rewrite_metadata_json(b'{"TEXTURE_SIZE":{"cm":{"width":12.9570,"height":13.8450}}}', values())
    assert b'"width": 12.9570' in result and b'"height": 13.8450' in result


def test_inventory_shortcuts_are_observed_names_not_the_examples_closed_enum():
    entries = [{"path": "8K", "kind": "directory"}, *[
        {"path": f"8K/{IDENTITY['BASE_NAME']}_{shortcut}_8K.jpg", "kind": "file"}
        for shortcut in ("COL", "DIFF", "ID", "SPEC")]]
    facts = metadata_inventory_facts(entries, IDENTITY)
    assert facts["MAPS_SHORTCUTS"] == ["COL", "DIFF", "ID", "SPEC"]
    assert facts["RESOLUTIONS"]["8K"]["UNRECOGNIZED"] == []


@pytest.mark.parametrize("raw", [b'{"COLOR":{},"COLOR":{}}', b'{"TEXTURE_SIZE":{"in":false}}', b'{"x":NaN}', b'{"WEB_APP_PART":{}}'])
def test_unsupported_json_is_never_silently_discarded(raw):
    with pytest.raises(ValueError, match="METADATA_FORMAT_UNSUPPORTED"):
        rewrite_metadata_json(raw, values(), IDENTITY)


def test_backend_and_worker_ship_identical_pure_transform():
    root = Path(__file__).resolve().parents[2]
    assert (root / "backend/app/metadata_document.py").read_bytes() == (root / "worker/app/metadata_document.py").read_bytes()
