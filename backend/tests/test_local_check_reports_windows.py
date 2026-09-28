import os
from pathlib import Path

import pytest

from app.local_check_reports import LocalCheckReports, _validated_report_target
from app.local_filesystem import LocalFilesError
from app.local_materials import LocalMaterialLibrary

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows reports adapter")


def test_default_report_root_is_verified_and_saves_in_fixed_namespace(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    local.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    result = LocalCheckReports().save("Default report: žlutá", open_report=False)
    path = Path(result["report_path"])
    assert path.parent == local / "REAWOTE" / "Reports" / "Checks"
    assert path.read_text(encoding="utf-8-sig") == "Default report: žlutá"
    assert result["report_opened"] is False


def test_msix_report_target_uses_physical_path_without_weakening_source_filesystem(tmp_path):
    local = tmp_path / "Local"
    physical = local / "Packages" / "OpenAI.Codex_2p2nqsd0c76g0" / "LocalCache" / "Local" / "REAWOTE"
    assert _validated_report_target(local, "\\\\?\\" + str(physical)) == physical
    assert _validated_report_target(local, "\\\\?\\" + str(local / "REAWOTE")) == local / "REAWOTE"


@pytest.mark.parametrize("suffix", [
    "Other/REAWOTE",
    "Packages/OpenAI.Codex/LocalCache/Roaming/REAWOTE",
    "Packages/OpenAI.Codex/LocalCache/Local/Other",
    "Packages/OpenAI.Codex/LocalCache/Local/REAWOTE/Extra",
])
def test_report_target_rejects_unexpected_redirection(tmp_path, suffix):
    local = tmp_path / "Local"
    with pytest.raises(LocalFilesError, match="LOCAL_REPORTS_UNSAFE"):
        _validated_report_target(local, "\\\\?\\" + str(local / suffix))


def test_report_target_rejects_other_profile_and_non_native_path(tmp_path):
    local = tmp_path / "Local"
    with pytest.raises(LocalFilesError, match="LOCAL_REPORTS_UNSAFE"):
        _validated_report_target(local, "\\\\?\\" + str(tmp_path / "Other" / "REAWOTE"))
    with pytest.raises(LocalFilesError, match="LOCAL_REPORTS_UNSAFE"):
        _validated_report_target(local, str(local / "REAWOTE"))


def test_default_report_root_refuses_existing_reparse_directory(tmp_path, monkeypatch):
    from _winapi import CreateJunction

    local = tmp_path / "Local"
    local.mkdir()
    target = tmp_path / "Outside"
    target.mkdir()
    link = local / "REAWOTE"
    CreateJunction(str(target), str(link))
    try:
        monkeypatch.setenv("LOCALAPPDATA", str(local))
        with pytest.raises(LocalFilesError, match="LOCAL_REPORTS_UNSAFE"):
            LocalCheckReports().save("Must not be written")
    finally:
        link.rmdir()
    assert not list(target.iterdir())


def test_reports_are_unique_persistent_files_outside_material_sources_and_open_only_on_request(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("WINDIR", "C:\\Windows")
    monkeypatch.setattr("app.local_check_reports.subprocess.Popen", lambda *args, **kwargs: calls.append(args))
    reports = LocalCheckReports(tmp_path / "Private" / "REAWOTE" / "Reports" / "Checks")
    first = reports.save("First report: žluťoučký", open_report=False)
    assert not first["report_opened"] and not calls
    second = reports.save("Second report")
    assert second["report_opened"] and first["report_path"] != second["report_path"]
    assert Path(first["report_path"]).read_text(encoding="utf-8-sig") == "First report: žluťoučký"
    assert Path(second["report_path"]).read_text(encoding="utf-8-sig") == "Second report"
    assert calls == [(["C:\\Windows\\System32\\notepad.exe", second["report_path"]],)]


def test_failed_editor_launch_preserves_downloadable_report(tmp_path, monkeypatch):
    monkeypatch.setattr("app.local_check_reports.subprocess.Popen", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no editor")))
    result = LocalCheckReports(tmp_path / "reports").save("Completed report")
    assert not result["report_opened"]
    assert Path(result["report_path"]).read_text(encoding="utf-8-sig") == "Completed report"


def test_preliminary_check_reads_local_sources_without_creating_or_modifying_them(tmp_path):
    folder = "Brand/BRAND_0001_MATERIAL_K03"
    root = tmp_path / "library"
    (root / folder / "4K").mkdir(parents=True)
    source = root / folder / "4K" / "BRAND_0001_MATERIAL_COL_4K.png"
    source.write_bytes(b"unchanged original texture")
    before = [(p.relative_to(root).as_posix(), p.read_bytes()) for p in root.rglob("*") if p.is_file()]
    library = LocalMaterialLibrary(root, tmp_path / "journal")
    result = library.check(folder)
    assert "metadata.json: MISSING" in result["issues"]
    assert any("No supported previews" in item for item in result["issues"])
    assert [(p.relative_to(root).as_posix(), p.read_bytes()) for p in root.rglob("*") if p.is_file()] == before
