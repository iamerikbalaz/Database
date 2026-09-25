import pytest

from app.material_naming import base_name, build_identity, match_identity


@pytest.mark.parametrize("identity,prefix,number,name,category", [
    ("TEST_0021_03-BRUSHED-GOLD_K03", "TEST", "0021", "03-BRUSHED-GOLD", "K03"),
    ("TEST_GROUP_0021_03.Brushed-Gold_K03", "TEST_GROUP", "0021", "03.Brushed-Gold", "K03"),
    ("TEST_0021_1234_K03", "TEST", "0021", "1234", "K03"),
    ("TEST_GROUP_0021_K03", "TEST_GROUP", "0021", None, "K03"),
])
def test_reads_existing_identity_without_changing_its_spelling(identity, prefix, number, name, category):
    match = match_identity(identity)
    assert match is not None
    assert (match["prefix"], match["number"], match.groupdict().get("name"), match["category"]) == (prefix, number, name, category)
    assert base_name(identity) == (identity.rsplit("_", 1)[0] if name else identity)


@pytest.mark.parametrize("identity", ["TEST_0000_NAME_K03", "TEST_21_NAME_K03", "TEST_0021__K03",
    "TEST_0021_../NAME_K03", "TEST_0021_A\\B_K03", "TEST_0021_A:B_K03", "TEST_0021_A B_K03",
    "TEST_0021_NAME_k03", "TEST_0021_NAME_K03\n", "TEST_0021_..._K03", "A" * 245 + "_0021_NAME_K03"])
def test_rejects_unsafe_or_unusable_folder_identities(identity):
    assert match_identity(identity) is None


def test_creation_includes_name_and_normalizes_only_the_generated_component():
    assert build_identity("TEST", 21, "K03", " 03 Brushed Gold ") == "TEST_0021_03-BRUSHED-GOLD_K03"
    assert build_identity("TEST", 22, "K03", "Žlutá žula 2.5 / leštěná") == "TEST_0022_ZLUTA-ZULA-2.5-LESTENA_K03"
    with pytest.raises(ValueError, match="^MATERIAL_IDENTITY_INVALID$"):
        build_identity("TEST", 23, "K03", "---")
    with pytest.raises(ValueError, match="^MATERIAL_IDENTITY_INVALID$"):
        build_identity("TEST", 23, "K03", "x" * 255)


def test_rebrand_and_category_change_keep_original_name_despite_edited_display_name():
    assert build_identity("NEXT", 4, "K04", "Edited display name", source_identity="TEST_0021_03.Brushed-Gold_K03") == "NEXT_0004_03.Brushed-Gold_K04"
