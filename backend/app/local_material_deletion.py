"""Atomic removal from the material library into same-volume recovery quarantine.

Only a database-bound material folder is accepted. A durable journal pins the
directory identity before moving it; replay never touches a replacement folder.
"""
from pathlib import PurePosixPath
from uuid import UUID, uuid5
from app.local_filesystem import LocalFilesError, signature


class LocalMaterialDeletion:
    def __init__(self, library): self.library = library

    def snapshot(self, folder):
        fs = self.library.fs
        if len(PurePosixPath(folder).parts) < 2: raise LocalFilesError("MATERIAL_DELETE_FOLDER_UNSAFE")
        with self.library.lock, fs.tree(folder, hash_files=False, for_rename=False) as (path, entries, handles):
            # No full texture decoding/hashing: the deletion selects the complete
            # folder, and is pinned to the directory plus its observed inventory.
            return {"directory_identity": fs.identity(handles[""]), "entries": [
                {**item, "identity": fs.identity(handles[item["path"]]),
                 "signature": list(signature((path / item["path"]).stat()))} for item in entries]}

    def execute(self, operation_id, item):
        library = self.library; fs = library.fs
        key = str(uuid5(UUID(str(operation_id)), "material-delete:" + item["material_id"]))
        request = {"kind": "MATERIAL_DELETE", "operation_id": str(operation_id), "item": item}
        source = fs.path(item["folder_path"])
        if source == fs.root or not source.is_relative_to(fs.root): raise LocalFilesError("MATERIAL_DELETE_FOLDER_UNSAFE")
        with library.lock:
            key, state = library.journal_state(key, request)
            if state.get("result"): return state["result"]
            with library.journal.directory(key) as journal:
                saved = journal / "data"
                if "authorized_identity" not in state:
                    if saved.exists(): raise LocalFilesError("MATERIAL_DELETE_QUARANTINE_CHANGED")
                    try: current = self.snapshot(item["folder_path"])
                    except (OSError, ValueError, LocalFilesError): current = None
                    if current != item["snapshot"]:
                        state["result"] = {"status":"REJECTED", "error_code":"MATERIAL_DELETE_SOURCE_CHANGED"}
                        library.write_state(key, state); return state["result"]
                    state["authorized_identity"] = current["directory_identity"]
                    library.write_state(key, state)
                at_source, at_saved = source.exists(), saved.exists()
                if at_saved:
                    with library.journal.opened(saved, directory=True) as handle:
                        if library.journal.identity(handle) != state["authorized_identity"]:
                            raise LocalFilesError("MATERIAL_DELETE_QUARANTINE_CHANGED")
                    # The original may already have moved before a process crash.
                    # A subsequently recreated source path belongs to someone else.
                elif at_source:
                    parent = source.parent.relative_to(fs.root).as_posix()
                    with fs.directory(parent), fs.opened(source, directory=True, rename=True) as handle:
                        if fs.identity(handle) != state["authorized_identity"]:
                            raise LocalFilesError("MATERIAL_DELETE_SOURCE_CHANGED")
                        fs.rename_handle(handle, saved)
                else: raise LocalFilesError("MATERIAL_DELETE_SOURCE_MISSING")
                state["result"] = {"status":"COMPLETED", "error_code":None}
                library.write_state(key, state)
                return state["result"]
