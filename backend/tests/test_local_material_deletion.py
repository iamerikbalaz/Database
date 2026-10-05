import os
from uuid import uuid4,uuid5
import pytest
from app.local_materials import LocalMaterialLibrary
from app.local_material_deletion import LocalMaterialDeletion
from app.local_filesystem import LocalFilesError

pytestmark=pytest.mark.skipif(os.name!="nt",reason="Real Windows filesystem handle guards")
FOLDER="Brand/BRAND_0001_SAMPLE_K03"


@pytest.fixture
def files(tmp_path):
    directory=tmp_path/"library"/FOLDER
    (directory/"SOURCE").mkdir(parents=True)
    (directory/"SOURCE"/"sample.sbs").write_bytes(b"source data retained")
    (directory/"PREVIEW").mkdir()
    (directory/"PREVIEW"/"SPHERE_1.png").write_bytes(b"preview data retained")
    (directory/"metadata.json").write_bytes(b"{}")
    library=LocalMaterialLibrary(tmp_path/"library",tmp_path/"journal")
    adapter=LocalMaterialDeletion(library)
    item={"material_id":str(uuid4()),"folder_path":FOLDER,"snapshot":adapter.snapshot(FOLDER)}
    return library,adapter,item


def saved_folder(library,operation,item):
    return library.journal.root/str(uuid5(operation,"material-delete:"+item["material_id"]))/"data"


def test_whole_material_is_atomically_quarantined_including_source_and_replay_is_exact(files):
    library,adapter,item=files; operation=uuid4(); original=library.fs.path(FOLDER)
    before={path.relative_to(original).as_posix():path.read_bytes() for path in original.rglob("*") if path.is_file()}
    result=adapter.execute(operation,item)
    assert result=={"status":"COMPLETED","error_code":None} and not original.exists()
    saved=saved_folder(library,operation,item)
    after={path.relative_to(saved).as_posix():path.read_bytes() for path in saved.rglob("*") if path.is_file()}
    assert after==before
    assert adapter.execute(operation,item)==result
    assert (library.fs.root/"Brand").exists()


@pytest.mark.parametrize("change",["new-file","changed-file","replaced-folder"])
def test_changed_review_plan_never_moves_new_content(files,change):
    library,adapter,item=files; source=library.fs.path(FOLDER)
    if change=="new-file": (source/"later.txt").write_text("new")
    elif change=="changed-file": (source/"metadata.json").write_text("changed")
    else: source.rename(source.with_name("original")); source.mkdir()
    operation=uuid4(); result=adapter.execute(operation,item)
    assert result["status"]=="REJECTED" and source.exists() and not saved_folder(library,operation,item).exists()


@pytest.mark.parametrize("boundary",["after-authorize","after-rename","after-result"])
def test_interruption_recovers_without_moving_a_replacement_folder(files,monkeypatch,boundary):
    library,adapter,item=files; operation=uuid4(); original_write=library.write_state; original_rename=library.fs.rename_handle
    failed=False
    def write(key,state):
        nonlocal failed
        original_write(key,state)
        if not failed and ((boundary=="after-authorize" and "result" not in state) or (boundary=="after-result" and "result" in state)):
            failed=True; raise OSError("Synthetic interrupted journal")
    def rename(handle,target):
        nonlocal failed
        original_rename(handle,target)
        if not failed and boundary=="after-rename": failed=True; raise OSError("Synthetic interrupted rename")
    monkeypatch.setattr(library,"write_state",write); monkeypatch.setattr(library.fs,"rename_handle",rename)
    with pytest.raises(OSError): adapter.execute(operation,item)
    source=library.fs.path(FOLDER)
    if not source.exists(): source.mkdir(); (source/"replacement.txt").write_text("untouched replacement")
    result=adapter.execute(operation,item)
    assert result["status"]=="COMPLETED" and (saved_folder(library,operation,item)/"SOURCE"/"sample.sbs").read_bytes()==b"source data retained"
    if boundary!="after-authorize": assert (source/"replacement.txt").read_text()=="untouched replacement"


def test_modified_operation_and_quarantine_identity_are_not_replayed(files):
    library,adapter,item=files; operation=uuid4(); adapter.execute(operation,item)
    with pytest.raises(LocalFilesError,match="REQUEST_CONFLICT"):
        adapter.execute(operation,{**item,"folder_path":"Brand/DIFFERENT_0001"})


def test_recovery_refuses_a_replaced_quarantine_directory(files,monkeypatch):
    library,adapter,item=files; operation=uuid4(); original_rename=library.fs.rename_handle
    def interrupt_after_move(handle,target):
        original_rename(handle,target)
        raise OSError("Synthetic interruption after moving the original")
    monkeypatch.setattr(library.fs,"rename_handle",interrupt_after_move)
    with pytest.raises(OSError): adapter.execute(operation,item)
    saved=saved_folder(library,operation,item)
    original=saved.with_name("original-retained")
    saved.rename(original)
    saved.mkdir(); (saved/"replacement.txt").write_text("unrelated data")
    with pytest.raises(LocalFilesError,match="MATERIAL_DELETE_QUARANTINE_CHANGED"):
        adapter.execute(operation,item)
    assert (original/"SOURCE"/"sample.sbs").read_bytes()==b"source data retained"
    assert (saved/"replacement.txt").read_text()=="unrelated data"


def test_hardlinks_and_unsafe_or_brand_root_paths_are_rejected(files,tmp_path):
    library,adapter,item=files
    external=tmp_path/"private.txt"; external.write_text("private")
    os.link(external,library.fs.path(FOLDER)/"linked.txt")
    with pytest.raises(LocalFilesError): adapter.snapshot(FOLDER)
    for path in ("", "Brand", "../outside", "C:/outside", "/outside"):
        with pytest.raises((LocalFilesError,ValueError)): adapter.snapshot(path)
    assert external.read_text()=="private"


def test_open_source_files_prevent_mutation_until_closed(files):
    library,adapter,item=files; operation=uuid4()
    with library.fs.opened(library.fs.path(FOLDER)/"metadata.json"):
        # A complete-folder move cannot bypass an external handle which denies
        # delete sharing; either inspection or the atomic move fails safely.
        with pytest.raises((LocalFilesError,OSError)): adapter.execute(operation,item)
    assert adapter.execute(operation,item)["status"]=="COMPLETED"
