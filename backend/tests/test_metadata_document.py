from decimal import Decimal
import json
from pathlib import Path

import pytest

from app.metadata_client import MetadataValues
from app.metadata_document import rewrite_metadata_json


IDENTITY = {"FOLDER": "BRAND_0042_A-SAMPLE_G03", "MANUFACTURER": "Brand", "PRODUCT_NUMBER": "0042",
    "PRODUCT_NAME": "A sample", "CATEGORY": "G03", "BASE_NAME": "BRAND_0042_A-SAMPLE"}


def values(**changes):
    return MetadataValues(**{"hex_color": "#ABCDEF", "width_cm": "12.957", "height_cm": "13.845", **changes}).model_dump()


def test_bootstrap_uses_real_identity_and_has_no_invented_measurements():
    document = json.loads(rewrite_metadata_json(None, values(), IDENTITY))
    assert {key: document[key] for key in IDENTITY} == IDENTITY
    assert document["COLOR"] == {"hex": "#ABCDEF", "method": "database"}
    assert document["TEXTURE_SIZE"]["cm"] == {"width": 12.957, "height": 13.845}
    assert document["TEXTURE_SIZE"]["in"] == {"width": 5.1012, "height": 5.4508}
    assert "MAPS_SHORTCUTS" not in document and "SOURCE" not in document


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
    assert document["COLOR"] == {"hex": "#ABCDEF", "delta_e": 5.86, "method": "chromatic"}


def test_manual_edit_updates_derived_inches_and_removes_stale_selection_distances():
    raw = b'{"COLOR":{"hex":"#999999","delta_e":5.86,"runner_up":{"hex":"#CCCC99"},"average_rgb":[171,171,167],"measured_from":"map.jpg","method":"chromatic"},"TEXTURE_SIZE":{"cm":{"width":12.957,"height":13.845},"in":{"width":5.1011,"height":5.4507},"method":"sbs"}}'
    document = json.loads(rewrite_metadata_json(raw, values(width_cm="10.2", height_cm=None), IDENTITY))
    assert document["TEXTURE_SIZE"]["in"] == {"width": 4.0157}
    assert document["TEXTURE_SIZE"]["cm"] == {"width": 10.2}
    assert document["TEXTURE_SIZE"]["method"] == "manual"
    assert document["COLOR"]["average_rgb"] == [171, 171, 167]
    assert document["COLOR"]["measured_from"] == "map.jpg"
    assert "delta_e" not in document["COLOR"] and "runner_up" not in document["COLOR"]


@pytest.mark.parametrize("raw", [b'{"COLOR":{},"COLOR":{}}', b'{"TEXTURE_SIZE":{"in":false}}', b'{"x":NaN}', b'{"WEB_APP_PART":{}}'])
def test_unsupported_json_is_never_silently_discarded(raw):
    with pytest.raises(ValueError, match="METADATA_FORMAT_UNSUPPORTED"):
        rewrite_metadata_json(raw, values(), IDENTITY)


def test_backend_and_worker_ship_identical_pure_transform():
    root = Path(__file__).resolve().parents[2]
    assert (root / "backend/app/metadata_document.py").read_bytes() == (root / "worker/app/metadata_document.py").read_bytes()
