import hashlib
import os
from uuid import uuid4

import pytest

from app.local_filesystem import LocalFilesError
from app.local_material_creation import LocalMaterialCreator
from app.local_materials import LocalMaterialLibrary

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows desktop creation adapter")


def setup(tmp_path):
    root = tmp_path / "materials"; root.mkdir()
    library = LocalMaterialLibrary(root, tmp_path / "journal")
    creator = LocalMaterialCreator(library)
    batch = uuid4(); raw = b"<synthetic-sbs />"
    context = {"materials_root": str(root), "customer_folder": "CUSTOMER", "template_name":"base.sbs", "template_sha256":hashlib.sha256(raw).hexdigest(),"resolution":7}
    item = {"material_id":str(uuid4()),"folder_path":"CUSTOMER/CUSTOMER_0001_NEW-MATERIAL_F01"}
    creator.capture_template(batch, context, raw)
    return library,creator,batch,context,item,raw


def test_folder_layout_template_bytes_and_replay_never_overwrites_user_edits(tmp_path):
    library,creator,batch,context,item,raw = setup(tmp_path)
    creator.create_folder(batch,item,context)
    folder = library.fs.path(item["folder_path"])
    assert {str(path.relative_to(folder)).replace("\\","/") for path in folder.rglob("*")} == {"7K","PREVIEW","SOURCE","SOURCE/base.sbs"}
    assert (folder/"SOURCE/base.sbs").read_bytes() == raw
    (folder/"SOURCE/base.sbs").write_bytes(b"user edit")
    assert creator.create_folder(batch,item,context) == item["folder_path"]
    assert (folder/"SOURCE/base.sbs").read_bytes() == b"user edit"


def test_existing_unowned_folder_is_never_adopted_or_overwritten(tmp_path):
    library,creator,batch,context,item,raw = setup(tmp_path)
    folder = library.fs.path(item["folder_path"]); folder.mkdir(parents=True)
    (folder/"keep.txt").write_bytes(b"existing")
    with pytest.raises(LocalFilesError,match="MATERIAL_FOLDER_EXISTS"):
        creator.create_folder(batch,item,context)
    assert list(folder.iterdir()) == [folder/"keep.txt"]


def test_configured_nested_root_creates_new_folders_without_moving_existing_materials(tmp_path):
    library,creator,batch,context,item,raw = setup(tmp_path)
    selected = library.fs.root / "Next collection"; selected.mkdir()
    context = {**context, "materials_root": str(selected)}
    batch = uuid4()
    creator.capture_template(batch,context,raw)
    item = {**item, "folder_path": "Next collection/" + item["folder_path"]}
    creator.create_folder(batch,item,context)
    assert library.fs.path(item["folder_path"]).joinpath("SOURCE/base.sbs").read_bytes() == raw
    assert not (library.fs.root/"CUSTOMER").exists()


def test_crash_after_atomic_move_recovers_original_folder_identity(tmp_path,monkeypatch):
    library,creator,batch,context,item,raw = setup(tmp_path)
    write = library.write_state
    def interrupted(key,state):
        if state.get("complete"): raise OSError("synthetic crash after rename")
        return write(key,state)
    monkeypatch.setattr(library,"write_state",interrupted)
    with pytest.raises(OSError): creator.create_folder(batch,item,context)
    monkeypatch.setattr(library,"write_state",write)
    assert creator.create_folder(batch,item,context) == item["folder_path"]
    assert library.fs.path(item["folder_path"]).joinpath("SOURCE/base.sbs").read_bytes() == raw


def test_template_traversal_and_changed_snapshot_are_refused(tmp_path):
    library,creator,batch,context,item,raw = setup(tmp_path)
    templates=tmp_path/"templates"; templates.mkdir(); (templates/"base.sbs").write_bytes(raw)
    assert creator.read_template(str(templates),"base.sbs") == raw
    with pytest.raises(ValueError): creator.read_template(str(templates),"../base.sbs")
    library.journal.path(str(batch)+"/template.sbs").write_bytes(b"tampered")
    with pytest.raises(LocalFilesError,match="MATERIAL_TEMPLATE_SNAPSHOT_CHANGED"):
        creator.create_folder(batch,item,context)


def test_template_above_metadata_read_limit_is_copied_and_recovered(tmp_path, monkeypatch):
    library,creator,_,context,item,_ = setup(tmp_path)
    batch = uuid4()
    raw = b"<synthetic-sbs>" + b"x" * (5 * 1024 * 1024) + b"</synthetic-sbs>"
    context = {**context, "template_sha256": hashlib.sha256(raw).hexdigest()}
    creator.capture_template(batch,context,raw)
    creator.capture_template(batch,context,raw)
    rename = library.journal.rename_handle
    monkeypatch.setattr(library.journal,"rename_handle",lambda *_: (_ for _ in ()).throw(OSError("synthetic interruption before rename")))
    with pytest.raises(OSError): creator.create_folder(batch,item,context)
    monkeypatch.setattr(library.journal,"rename_handle",rename)
    creator.create_folder(batch,item,context)
    assert library.fs.path(item["folder_path"]).joinpath("SOURCE/base.sbs").read_bytes() == raw
