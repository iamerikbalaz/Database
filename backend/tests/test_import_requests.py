import base64
import json
from uuid import uuid4

from pydantic import ValidationError
import pytest

from app.import_requests import ImportColumns, ImportLinks, ImportSource, InspectImport, ConfirmImport, inspect_source, prepare_rows
from app.import_sources import ImportSourceError, read_csv

COLUMNS = {"identity": "Identity", "name": "Name", "project": "Project", "brand": "Brand", "processor": "Processor"}


def source(rows="RWT_0007_G03; Dub ;Project A;Brand A;Processor A", extra=""):
    return ("Identity;Name;Project;Brand;Processor" + extra + "\n" + rows).encode("utf-8")


def mapping():
    return ImportLinks(projects={"Project A": uuid4()}, brands={"Brand A": uuid4()}, processors={"Processor A": uuid4()})


def csv_payload(data=None):
    return ImportSource(format="CSV", data=base64.b64encode(source() if data is None else data).decode(), delimiter=";")


def test_explicit_mapping_preserves_identity_and_resolves_only_supplied_resource_ids():
    links = mapping()
    rows, findings = prepare_rows(read_csv(source(), delimiter=";"), ImportColumns(**COLUMNS), links)
    assert not findings and len(rows) == 1
    item = rows[0]
    assert item.technical_identity == "RWT_0007_G03" and item.sequence_number == 7 and item.prefix == "RWT"
    assert item.material_name == "Dub" and item.project_id == links.projects["Project A"]
    assert item.brand_id == links.brands["Brand A"] and item.processor_id == links.processors["Processor A"]
    assert "Dub" not in repr(item)


def test_mapping_does_not_guess_from_names_prefixes_or_another_resource_group():
    links = ImportLinks(projects={}, brands={"Brand A": uuid4()}, processors={})
    rows, findings = prepare_rows(read_csv(source(), delimiter=";"), ImportColumns(**COLUMNS), links)
    assert rows == []
    assert {item["field"] for item in findings} == {"project", "processor"}
    assert all(item["code"] == "IMPORT_REFERENCE_UNMAPPED" and item["row"] == 2 for item in findings)


@pytest.mark.parametrize("identity", ["RWT_7_G03", "RWT_0000_G03", "RWT_0007_g03", "RWT_0007_G__03", "RWT_0007_", "PRIVATE_SYNTHETIC",
    "../RWT_0007_G03", "nested/RWT_0007_G03", "nested\\RWT_0007_G03", "C:RWT_0007_G03", "RWT\tTEST_0007_G03"])
def test_invalid_historical_identity_is_not_silently_renumbered_or_reflected(identity):
    rows, findings = prepare_rows(read_csv(source(identity + ";Dub;Project A;Brand A;Processor A"), delimiter=";"), ImportColumns(**COLUMNS), mapping())
    assert not rows and findings
    assert "PRIVATE_SYNTHETIC" not in json.dumps(findings)


def test_prefixes_with_underscores_and_four_digit_upper_boundary_are_preserved():
    rows, findings = prepare_rows(read_csv(source("ACME_TEST_9999_G03;Dub;Project A;Brand A;Processor A"), delimiter=";"), ImportColumns(**COLUMNS), mapping())
    assert not findings and rows[0].prefix == "ACME_TEST" and rows[0].sequence_number == 9999


def test_duplicate_numbers_are_detected_even_with_different_category_suffixes():
    data = source("RWT_0007_G03;Dub;Project A;Brand A;Processor A\nRWT_0007_G04;Stone;Project A;Brand A;Processor A")
    rows, findings = prepare_rows(read_csv(data, delimiter=";"), ImportColumns(**COLUMNS), mapping())
    assert len(rows) == 1
    assert findings == [{"row": 3, "field": "identity", "code": "IMPORT_DUPLICATE_IDENTITY_OR_NUMBER"}]


def test_named_folder_import_keeps_exact_name_and_still_blocks_brand_number_collisions():
    data = source("RWT_0021_03.Brushed-Gold_K03;Display name;Project A;Brand A;Processor A\nRWT_0021_OTHER_K04;Other;Project A;Brand A;Processor A")
    rows, findings = prepare_rows(read_csv(data, delimiter=";"), ImportColumns(**COLUMNS), mapping())
    assert len(rows) == 1
    assert rows[0].technical_identity == "RWT_0021_03.Brushed-Gold_K03"
    assert (rows[0].prefix, rows[0].sequence_number, rows[0].main_category_code) == ("RWT", 21, "K03")
    assert rows[0].material_name == "Display name"
    assert findings == [{"row": 3, "field": "identity", "code": "IMPORT_DUPLICATE_IDENTITY_OR_NUMBER"}]


@pytest.mark.parametrize("folder", ["../RWT_0021_GOLD_K03", "R:/library/RWT_0021_GOLD_K03", "/library/RWT_0021_GOLD_K03", "library/OTHER_0021_GOLD_K03", "library\\RWT_0021_GOLD_K03", "", "library//RWT_0021_GOLD_K03"])
def test_import_folder_references_must_be_relative_and_bound_to_the_exact_identity(folder):
    data = source("RWT_0021_GOLD_K03;Gold;Project A;Brand A;Processor A;" + folder, extra=";Folder")
    rows, findings = prepare_rows(read_csv(data, delimiter=";"), ImportColumns(**COLUMNS, folder="Folder"), mapping())
    assert not rows and findings == [{"row": 2, "field": "identity", "code": "IMPORT_FOLDER_REFERENCE_INVALID"}]


def test_blank_or_overlong_names_block_the_row():
    for name in (" ", "x" * 256):
        rows, findings = prepare_rows(read_csv(source("RWT_0007_G03;" + name + ";Project A;Brand A;Processor A"), delimiter=";"), ImportColumns(**COLUMNS), mapping())
        assert not rows and findings[0]["code"] == "IMPORT_MATERIAL_NAME"


def test_headers_must_exist_and_map_to_distinct_columns():
    with pytest.raises(ValidationError):
        ImportColumns(**{**COLUMNS, "name": "Identity"})
    with pytest.raises(ImportSourceError, match="^IMPORT_COLUMN_MAPPING$"):
        prepare_rows(read_csv(source(), delimiter=";"), ImportColumns(**{**COLUMNS, "name": "Missing"}), mapping())


def test_normalized_reference_keys_cannot_be_ambiguous_or_invisible():
    identifier = uuid4()
    links = ImportLinks(projects={" Project A ": identifier}, brands={}, processors={})
    assert links.projects == {"Project A": identifier}
    for values in ({"Project A": identifier, " Project A ": identifier}, {"A\u202e": identifier}, {" ": identifier}):
        with pytest.raises(ValidationError):
            ImportLinks(projects=values, brands={}, processors={})


def test_inspection_returns_a_bounded_sample_and_explicit_mapping_choices():
    data = source("\n".join(f"RWT_{i:04d}_G03;Dub;Project A;Brand A;Processor A" for i in range(1, 15)))
    result = inspect_source(InspectImport(source=csv_payload(data), columns=ImportColumns(**COLUMNS)))
    assert result["row_count"] == 14 and len(result["sample"]) == 10
    assert result["mapping_values"] == {"project": ["Project A"], "brand": ["Brand A"], "processor": ["Processor A"]}
    assert "data" not in result and "data=" not in repr(csv_payload(data))


def test_inspection_limits_the_number_of_reference_groups():
    data = source("\n".join(f"RWT_{i:04d}_G03;Dub;Project {i};Brand A;Processor A" for i in range(1, 66)))
    with pytest.raises(ImportSourceError, match="^IMPORT_REFERENCE_LIMIT$"):
        inspect_source(InspectImport(source=csv_payload(data), columns=ImportColumns(**COLUMNS)))


def test_xlsx_inspection_requires_sheet_selection_before_returning_rows():
    from test_import_workbooks import archive
    source_value = ImportSource(format="XLSX", data=base64.b64encode(archive()).decode())
    result = inspect_source(InspectImport(source=source_value))
    assert result == {"format": "XLSX", "sheets": ("Materials",), "requires_sheet": True}


def test_source_options_and_base64_are_strict():
    for value in [{"format": "CSV", "data": "eA=="}, {"format": "XLSX", "data": "eA==", "delimiter": ";"},
                  {"format": "CSV", "data": "eA==", "delimiter": ";", "sheet": "Materials"}]:
        with pytest.raises(ValidationError):
            ImportSource(**value)
    for encoded in ("not base64", "é", "eA==\n"):
        with pytest.raises(ImportSourceError, match="^IMPORT_BASE64_INVALID$"):
            ImportSource(format="CSV", delimiter=";", data=encoded).decode()


def test_confirmation_requires_explicit_acknowledgement_reason_and_exact_hash():
    value = {"source": csv_payload().model_dump(), "columns": COLUMNS, "links": mapping().model_dump(mode="json"),
             "idempotency_key": str(uuid4()), "expected_preview_hash": "a" * 64, "acknowledge_unverified": True,
             "reason": "Reviewed historical import"}
    assert ConfirmImport.model_validate_json(json.dumps(value)).acknowledge_unverified is True
    for change in ({"acknowledge_unverified": False}, {"acknowledge_unverified": 1}, {"acknowledge_unverified": "true"},
                   {"reason": " "}, {"reason": "private\x00"}, {"reason": "private\u202e"},
                   {"expected_preview_hash": "bad"}, {"extra": "private"}):
        with pytest.raises(ValidationError):
            ConfirmImport.model_validate_json(json.dumps({**value, **change}))


@pytest.mark.parametrize("label", ["", "\"Project\nA\"", "Project\tA"])
def test_unmappable_source_reference_labels_are_reported_without_reflection(label):
    data = source("RWT_0007_G03;Dub;" + label + ";Brand A;Processor A")
    with pytest.raises(ImportSourceError, match="^IMPORT_REFERENCE_LABEL$"):
        inspect_source(InspectImport(source=csv_payload(data), columns=ImportColumns(**COLUMNS)))
