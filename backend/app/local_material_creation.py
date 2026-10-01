"""Owned, recoverable creation of new local material folders; never overwrites."""
import hashlib
import os
from pathlib import Path
from uuid import UUID, uuid5

from app.local_filesystem import LocalFilesystem, LocalFilesError
from app.path_settings import resolve_sbs_template

MAX_TEMPLATE_BYTES = 64 * 1024 * 1024


class LocalMaterialCreator:
    def __init__(self, library):
        self.library = library

    def read_template(self, root, name):
        path = resolve_sbs_template(root, name)
        return LocalFilesystem(path.parent).read(path.name, limit=MAX_TEMPLATE_BYTES)

    def capture_template(self, batch_id, context, raw):
        library = self.library
        with library.lock:
            key, state = library.journal_state(str(batch_id), {"kind": "MATERIAL_CREATE_TEMPLATE", "context": context})
            with library.journal.directory(key) as directory:
                target = directory / "template.sbs"
                if target.exists():
                    if library.journal.read(key + "/template.sbs", limit=MAX_TEMPLATE_BYTES) != raw:
                        raise LocalFilesError("MATERIAL_TEMPLATE_SNAPSHOT_CHANGED")
                else:
                    with open(target, "xb") as output:
                        output.write(raw); output.flush(); os.fsync(output.fileno())
                state["captured"] = True
                library.write_state(key, state)

    def create_folder(self, batch_id, item, context):
        library = self.library
        selected_root = Path(context["materials_root"])
        if not selected_root.is_relative_to(library.fs.root):
            raise LocalFilesError("LOCAL_MATERIALS_ROOT_CHANGED")
        prefix = selected_root.relative_to(library.fs.root).as_posix()
        prefix = "" if prefix == "." else prefix
        request = {"kind": "MATERIAL_FOLDER_CREATE", "batch_id": str(batch_id), "material_id": item["material_id"],
            "folder_path": item["folder_path"], "context": context}
        operation_id = uuid5(UUID(str(batch_id)), item["material_id"])
        with library.lock:
            key, state = library.journal_state(str(operation_id), request)
            library.write_state(key, state)
            destination = library.fs.path(item["folder_path"])
            customer = destination.parent.relative_to(library.fs.root).as_posix()
            if (destination.parent != selected_root / context["customer_folder"]
                    or len(Path(context["customer_folder"]).parts) != 1):
                raise LocalFilesError("LOCAL_PATH_UNSAFE")
            with library.fs.directory(prefix):
                destination.parent.mkdir(exist_ok=True)
            with library.fs.directory(customer):
                if destination.exists():
                    with library.fs.opened(destination, directory=True) as handle:
                        if state.get("directory_identity") != library.fs.identity(handle):
                            raise LocalFilesError("MATERIAL_FOLDER_EXISTS")
                    return item["folder_path"]
                if state.get("complete"):
                    raise LocalFilesError("MATERIAL_CREATED_FOLDER_MISSING")
                raw = library.journal.read(str(batch_id) + "/template.sbs", limit=MAX_TEMPLATE_BYTES)
                if hashlib.sha256(raw).hexdigest() != context["template_sha256"]:
                    raise LocalFilesError("MATERIAL_TEMPLATE_SNAPSHOT_CHANGED")
                stage_relative = key + "/material"
                stage = library.journal.path(stage_relative)
                with library.journal.directory(key):
                    stage.mkdir(exist_ok=True)
                expected_dirs = {"PREVIEW", "SOURCE"}
                # Only batches already frozen by an older version contain this.
                # Preserve their staged tree and journal request during recovery.
                if context.get("resolution") is not None:
                    expected_dirs.add(str(context["resolution"]) + "K")
                with library.journal.directory(stage_relative):
                    for name in expected_dirs:
                        (stage / name).mkdir(exist_ok=True)
                        with library.journal.directory(stage_relative + "/" + name):
                            pass
                source_relative = stage_relative + "/SOURCE"
                with library.journal.directory(source_relative) as source:
                    template = source / context["template_name"]
                    if template.exists():
                        if library.journal.read(source_relative + "/" + template.name, limit=MAX_TEMPLATE_BYTES) != raw:
                            raise LocalFilesError("MATERIAL_CREATION_STAGING_CHANGED")
                    else:
                        with open(template, "xb") as output:
                            output.write(raw); output.flush(); os.fsync(output.fileno())
                # A previous interruption can leave the same owned staging
                # tree. Extra files or replaced directories cannot be adopted.
                with library.journal.tree(stage_relative, for_rename=False) as (_, entries, _):
                    if {entry["path"] for entry in entries} != expected_dirs | {"SOURCE/" + context["template_name"]}:
                        raise LocalFilesError("MATERIAL_CREATION_STAGING_CHANGED")
                with library.journal.directory(key), library.journal.opened(stage, directory=True, rename=True) as handle:
                    identity = library.journal.identity(handle)
                    if state.get("directory_identity") not in (None, identity):
                        raise LocalFilesError("MATERIAL_CREATION_STAGING_CHANGED")
                    state["directory_identity"] = identity
                    library.write_state(key, state)
                    library.journal.rename_handle(handle, destination)
                state["complete"] = True
                library.write_state(key, state)
                return item["folder_path"]
