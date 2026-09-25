import pytest

from app.material_naming import base_name, match_identity
from app.secure_filesystem import secure_filesystem_access_supported


@pytest.mark.parametrize("identity,number,base", [
    ("TEST_0021_03-BRUSHED-GOLD_K03", "0021", "TEST_0021_03-BRUSHED-GOLD"),
    ("TEST_GROUP_0021_03.Brushed-Gold_K03", "0021", "TEST_GROUP_0021_03.Brushed-Gold"),
    ("TEST_0021_1234_K03", "0021", "TEST_0021_1234"),
    ("TEST_GROUP_0021_K03", "0021", "TEST_GROUP_0021_K03"),
])
def test_identity_contract_matches_backend(identity, number, base):
    assert match_identity(identity)["number"] == number
    assert base_name(identity) == base


@pytest.mark.parametrize("identity", ["TEST_0000_NAME_K03", "TEST_21_NAME_K03", "TEST_0021__K03",
    "TEST_0021_../NAME_K03", "TEST_0021_A\\B_K03", "TEST_0021_A:B_K03", "TEST_0021_A B_K03",
    "TEST_0021_NAME_k03", "TEST_0021_NAME_K03\n", "TEST_0021_..._K03", "A" * 245 + "_0021_NAME_K03"])
def test_rejects_unsafe_or_unusable_folder_identities(identity):
    assert match_identity(identity) is None


@pytest.mark.skipif(not secure_filesystem_access_supported(), reason="Linux source operations required")
def test_named_material_without_metadata_validates_packages_and_rebrands_real_files(tmp_path):
    from uuid import uuid4
    from PIL import Image
    from app.identity_plan import IdentityTarget, plan_identity_change
    from app.identity_execute import execute_identity_change
    from app.packaging_plan import build_packaging_plan
    from app.technical_validation import validate_material

    old = "TEST_0021_03-BRUSHED-GOLD_K03"
    new = "NEXT_0001_03-BRUSHED-GOLD_K04"
    root = tmp_path / "materials"
    folder = root / "brand" / old
    (folder / "1K").mkdir(parents=True)
    for shortcut in ("COL", "NRM", "ROUGH"):
        Image.new("RGB", (1024, 1024), "#607080").save(folder / "1K" / f"{base_name(old)}_{shortcut}_1K.png")
    before = {p.name: p.read_bytes() for p in (folder / "1K").iterdir()}
    report = validate_material(root, ("brand", old))
    assert report["can_approve"] and not report["errors"]
    assert "SOURCE_METADATA_MISSING" in {item["code"] for item in report["warnings"]}
    package = build_packaging_plan(report, expected_source_revision_hash=report["inventory"]["source_revision_hash"],
                                   policy="CURRENT_ON_OR_AFTER_2026_03_04")
    assert package.production_metadata is None
    assert {item.destination for item in package.resolutions[0].maps} == {f"1K/{base_name(old)}_{shortcut}_1K.png" for shortcut in ("COL", "NRM", "ROUGH")}
    target = IdentityTarget("brand/" + new, "Next brand", "Edited display name")
    plan = plan_identity_change(root, ("brand", old), target)
    assert plan["ready"] and len(plan["changes"]) == 3
    journal = tmp_path / "journal"
    journal.mkdir(mode=0o700)
    result = execute_identity_change(root, journal, str(uuid4()), ("brand", old), target, expected_plan_hash=plan["plan_hash"], enabled=True)
    assert result["status"] == "COMPLETED"
    assert not folder.exists() and not (root / "brand" / new / "metadata.txt").exists()
    assert {p.name.replace(base_name(new), base_name(old)): p.read_bytes() for p in (root / "brand" / new / "1K").iterdir()} == before
    assert validate_material(root, ("brand", new))["can_approve"]
