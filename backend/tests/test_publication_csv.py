import csv
import hashlib
import io
from dataclasses import FrozenInstanceError
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.publication_csv import CSV_COLUMNS, PublicationCsvRow, render_publication_csv


def row(**updates):
    return PublicationCsvRow(material_id=updates.pop("material_id", uuid4()), revision_hash="a" * 64,
        content_context_hash="b" * 64, **{"identity_name": "BRAND_0001_Clay_CAT_1K", "name": "Clay",
        "description": 'Text; with "quotes"\nand a new line.', "credits": 0, "width_cm": Decimal("12.3400"),
        "height_cm": Decimal("100.0000"), "brand_identifier": "EXAMPLE", "categories": ("Stone",),
        "color": "#A1B2C3", "tags": ("Clay",), **updates})


def parsed(artifact):
    return list(csv.reader(io.StringIO(artifact.data.decode("utf-8-sig"), newline=""), delimiter=";"))


def test_exact_nine_column_bom_semicolon_crlf_quoting_and_hash_contract():
    item = row(name="Žlutý kámen")
    artifact = render_publication_csv([item])
    assert artifact.data.startswith(b"\xef\xbb\xbfidentity_name;name;description;credits;dimension;")
    assert artifact.data.endswith(b"\r\n")
    assert b'"Text; with ""quotes""\nand a new line."' in artifact.data
    assert parsed(artifact) == [list(CSV_COLUMNS), ["BRAND_0001_Clay_CAT_1K", "Žlutý kámen",
        'Text; with "quotes"\nand a new line.', "0", "12.34x100 cm", "EXAMPLE", "Stone", "#A1B2C3", "Clay"]]
    assert len(parsed(artifact)[1]) == 9
    assert artifact.sha256 == hashlib.sha256(artifact.data).hexdigest()
    assert artifact.rows[0].material_id == item.material_id
    assert artifact.rows[0].revision_hash == "a" * 64 and artifact.rows[0].content_context_hash == "b" * 64
    assert str(item.material_id).encode() not in artifact.data and b"a" * 64 not in artifact.data
    assert artifact.warnings == ()


def test_normalized_individual_values_deduplicate_and_sort_without_extra_column():
    item = row(categories=("Stone", "stone", "  Ceramic "), tags=("glossy", "Clay", "clay", "Écru", "E\u0301cru"))
    assert parsed(render_publication_csv([item]))[1][6:] == ["Ceramic:Stone", "#A1B2C3", "Clay:glossy:Écru"]


def test_deterministic_selection_order_and_full_snapshot_digests():
    first = row()
    second = row(identity_name="BRAND_0002_Clay_CAT_1K")
    artifact = render_publication_csv([first, second])
    assert render_publication_csv([second, first]) == artifact
    changed = first.model_copy(update={"content_context_hash": "c" * 64})
    other = render_publication_csv([changed, second])
    assert other.data == artifact.data and other.sha256 == artifact.sha256
    assert other.rows[0].snapshot_hash != artifact.rows[0].snapshot_hash
    assert other.rows[1] == artifact.rows[1]


@pytest.mark.parametrize("width,height,expected", [("0.0001", "99999999.9999", "0.0001x99999999.9999 cm"),
    ("1E+2", "1E-4", "100x0.0001 cm"), ("2.50000", "2", "2.5x2 cm")])
def test_dimensions_never_use_exponent_comma_or_rounding(width, height, expected):
    assert parsed(render_publication_csv([row(width_cm=width, height_cm=height)]))[1][4] == expected


@pytest.mark.parametrize("value", [0, 1.2, True, "0", "-1", "0.00001", "99999999.99991", "100000000", "NaN", "Infinity", "1,2", None,
    "1.0000000000000000000000000000000001", "1E-999999999", "1." + "0" * 100])
def test_unrepresentable_or_inexact_dimensions_rejected(value):
    with pytest.raises(ValidationError): row(width_cm=value)


@pytest.mark.parametrize("updates", [{"categories": ()}, {"categories": ("Stone:Clay",)}, {"tags": ("two:tags",)},
    {"tags": ("invisible\x00",)}, {"name": "invalid\nname"}, {"brand_identifier": ""}, {"description": "control\x00"},
    {"credits": None}, {"credits": True}, {"credits": 1.1}, {"credits": -1}, {"credits": 2147483648},
    {"color": "#abcdef"}, {"color": "#12345"}, {"color": None}, {"tags": ("x",) * 101},
    {"categories": ("Stone",) * 101}, {"description": "x" * 10001}])
def test_invalid_export_values_never_render(updates):
    with pytest.raises(ValidationError): row(**updates)


def test_empty_optional_content_warns_but_does_not_invent_values():
    artifact = render_publication_csv([row(description=None, tags=())])
    assert parsed(artifact)[1][2] == "" and parsed(artifact)[1][8] == ""
    assert [item.code for item in artifact.warnings] == ["CONTENT_DESCRIPTION_EMPTY", "CONTENT_TAGS_EMPTY"]


def test_formula_like_cells_warn_without_changing_approved_importer_values():
    artifact = render_publication_csv([row(name="=1+1", description=" \t@SUM(1,2)")])
    assert parsed(artifact)[1][1:3] == ["=1+1", " \t@SUM(1,2)"]
    assert len(artifact.warnings) == 1
    assert artifact.warnings[0].code == "CSV_FORMULA_LIKE_VALUE"
    assert artifact.warnings[0].fields == ("name", "description")


def test_duplicate_materials_identities_and_unbounded_batches_fail():
    first = row()
    for rows in ([], [first, first], [first, row()], [first, row(identity_name=first.identity_name.lower())],
            [first, row(material_id=first.material_id, identity_name="DIFFERENT")], [first] * 101, [first.model_dump()]):
        with pytest.raises(ValueError): render_publication_csv(rows)


def test_caller_mutation_cannot_change_frozen_snapshot_or_csv():
    categories = ["Stone"]
    item = row(categories=categories)
    artifact = render_publication_csv([item])
    categories.append("Ceramic")
    assert item.categories == ("Stone",) and render_publication_csv([item]) == artifact
    with pytest.raises(ValidationError): item.name = "Changed"
    with pytest.raises(FrozenInstanceError): artifact.sha256 = "changed"


def test_equal_decimal_values_keep_identical_snapshot_hashes():
    identifier = uuid4()
    first = row(material_id=identifier, width_cm="1.00000")
    second = row(material_id=identifier, width_cm="1")
    assert render_publication_csv([first]) == render_publication_csv([second])


def test_technical_identity_preserves_existing_512_character_database_contract():
    identity = "A" * 512
    assert parsed(render_publication_csv([row(identity_name=identity)]))[1][0] == identity
    with pytest.raises(ValidationError): row(identity_name=identity + "A")
