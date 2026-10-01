"""Native picker process contract tested with no real dialog or filesystem changes."""
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
import os
import subprocess
import pytest
from app import native_folder_picker
from app.local_filesystem import LocalFilesError
from app.local_materials import LocalMaterialLibrary

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows native picker")


def test_picker_passes_path_and_description_as_data_and_disables_folder_creation(monkeypatch):
    captured = {}
    chosen = "C:\\Výběr\\Materiály"
    def run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return SimpleNamespace(returncode=0, stdout=chosen)
    monkeypatch.setattr(native_folder_picker.subprocess, "run", run)
    original = "C:\\Folder'$(not-a-command)"
    description = "Choose $(not-a-command)"
    assert native_folder_picker.select_native_directory(description, original) == chosen
    assert captured["env"]["REAWOTE_PICKER_ROOT"] == original
    assert captured["env"]["REAWOTE_PICKER_DESCRIPTION"] == description
    assert original not in captured["command"][-1] and description not in captured["command"][-1]
    assert "$d.ShowNewFolderButton=$false" in captured["command"][-1]
    assert captured["creationflags"] == 0x08000000 and captured["encoding"] == "utf-8"
    assert Path(captured["command"][0]).is_absolute()


@pytest.mark.parametrize("result,expected", [(SimpleNamespace(returncode=0, stdout=""), None), (SimpleNamespace(returncode=1, stdout="PRIVATE"), "LOCAL_PICKER_UNAVAILABLE"), (subprocess.TimeoutExpired("picker", 180), "LOCAL_PICKER_TIMEOUT")])
def test_picker_cancel_failures_and_lock_release(monkeypatch, result, expected):
    def run(*args, **kwargs):
        if isinstance(result, Exception): raise result
        return result
    monkeypatch.setattr(native_folder_picker.subprocess, "run", run)
    library = object.__new__(LocalMaterialLibrary); library.picker_lock = Lock()
    if expected:
        with pytest.raises(LocalFilesError) as error: library.select_settings_folder("C:\\Path", "Choose")
        assert error.value.code == expected
    else:
        assert library.select_settings_folder("C:\\Path", "Choose") is None
    assert library.picker_lock.acquire(False)
    library.picker_lock.release()


def test_settings_and_move_pickers_share_a_lock_and_move_keeps_its_containment_check(monkeypatch):
    library = object.__new__(LocalMaterialLibrary); library.picker_lock = Lock()
    calls = []
    def picker(*args): calls.append(args); return "D:\\Outside"
    monkeypatch.setattr(native_folder_picker, "select_native_directory", picker)
    library.picker_lock.acquire()
    with pytest.raises(LocalFilesError) as error: library.select_settings_folder("C:\\Allowed", "Choose")
    assert error.value.code == "LOCAL_PICKER_BUSY" and not calls
    library.picker_lock.release()
    def relative(value):
        assert value == "D:\\Outside"
        raise LocalFilesError("LOCAL_PATH_OUTSIDE_ROOT")
    library.fs = SimpleNamespace(root="C:\\Allowed", relative=relative)
    with pytest.raises(LocalFilesError) as error: library.select_destination()
    assert error.value.code == "LOCAL_PATH_OUTSIDE_ROOT"
