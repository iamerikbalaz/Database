"""Native PREVIEW-only edits, with retained quarantine and crash recovery."""
from contextlib import contextmanager, ExitStack
import hashlib
from uuid import UUID, uuid5

from app.local_filesystem import LocalFilesError
from app.preview_edits import png_basename

MAX_FILE_BYTES = 64 * 1024**2
MAX_TOTAL_BYTES = 512 * 1024**2


class LocalPreviewEdits:
    def __init__(self, library): self.library = library

    @contextmanager
    def _observed(self, folder, *, rename_names=()):
        fs = self.library.fs
        relative = folder + "/PREVIEW"
        with fs.directory(relative) as path, ExitStack() as held:
            directory_handle = held.enter_context(fs.opened(path, directory=True))
            names = sorted(entry.name for entry in path.iterdir())
            if len(names) > 512 or len({name.casefold() for name in names}) != len(names):
                raise LocalFilesError("PREVIEW_ENTRY_LIMIT_OR_COLLISION")
            files = []; handles = {}; total = 0
            for name in names:
                if not name.lower().endswith(".png"): continue
                try: png_basename(name)
                except ValueError: raise LocalFilesError("PREVIEW_UNSAFE_NAME") from None
                handle = held.enter_context(fs.opened(path / name, rename=name in rename_names))
                raw = fs.read_handle(handle, limit=MAX_FILE_BYTES)
                total += len(raw)
                if len(files) >= 64 or total > MAX_TOTAL_BYTES: raise LocalFilesError("PREVIEW_TOTAL_LIMIT")
                # Editing filenames must also work for damaged previews. The
                # automatic file check owns image decoding/validation; here the
                # held handle, identity and byte digest protect the source.
                files.append({"name": name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "identity": fs.identity(handle)})
                handles[name] = handle
            if names != sorted(entry.name for entry in path.iterdir()): raise LocalFilesError("PREVIEW_SOURCE_CHANGED")
            yield {"names": names, "files": files, "directory_identity": fs.identity(directory_handle)}, handles

    def snapshot(self, folder):
        with self.library.lock, self._observed(folder) as (snapshot, _): return snapshot

    def execute(self, operation_id, item):
        library = self.library; fs = library.fs
        key = str(uuid5(UUID(str(operation_id)), item["material_id"]))
        request = {"kind": "PREVIEW_EDIT", "operation_id": str(operation_id), "item": item}
        with library.lock:
            key, state = library.journal_state(key, request)
            if state.get("result"): return state["result"]
            if "phase" not in state:
                try:
                    with self._observed(item["folder_path"]) as (current, _):
                        if current != item["snapshot"]:
                            return {"status": "REJECTED", "error_code": "PREVIEW_SOURCE_CHANGED"}
                except (LocalFilesError, OSError, ValueError):
                    return {"status": "REJECTED", "error_code": "PREVIEW_SOURCE_CHANGED"}
                state["phase"] = "STAGING"
                library.write_state(key, state)
            preview_relative = item["folder_path"] + "/PREVIEW"
            expected = {entry["name"]: entry for entry in item["snapshot"]["files"]}
            with fs.directory(preview_relative) as preview, library.journal.directory(key) as journal:
                quarantine = journal / "quarantine"
                quarantine.mkdir(exist_ok=True)
                with library.journal.directory(key + "/quarantine"):
                    with fs.opened(preview, directory=True) as directory_handle:
                        if fs.identity(directory_handle) != item["snapshot"]["directory_identity"]:
                            raise LocalFilesError("PREVIEW_DIRECTORY_CHANGED")
                    if state["phase"] == "STAGING":
                        self._stage(key, item, expected, preview, quarantine)
                        state["phase"] = "APPLYING"; library.write_state(key, state)
                    self._finish(key, item, expected, preview, quarantine)
                    state["phase"] = "COMPLETED"
                    state["result"] = {"status": "COMPLETED", "error_code": None,
                        "renamed": sum(change["to"] is not None for change in item["changes"]),
                        "deleted": sum(change["to"] is None for change in item["changes"])}
                    library.write_state(key, state)
                    return state["result"]

    def _proof(self, filesystem, path, expected, *, rename=False):
        @contextmanager
        def proof():
            with filesystem.opened(path, rename=rename) as handle:
                if (filesystem.identity(handle) != expected["identity"]
                        or filesystem.read_handle(handle, limit=MAX_FILE_BYTES, digest_only=True) != expected["sha256"]):
                    raise LocalFilesError("PREVIEW_SOURCE_CHANGED")
                yield handle
        return proof()

    def _stage(self, key, item, expected, preview, quarantine):
        fs = self.library.fs; journal = self.library.journal
        # First verify every current entry against its expected original or
        # quarantined location before moving any further file on recovery.
        selected = {change["from"] for change in item["changes"]}
        staged = set(); handles = {}
        with ExitStack() as held:
            for index, change in enumerate(item["changes"]):
                saved = quarantine / f"{index:04d}.png"
                if saved.exists():
                    held.enter_context(self._proof(journal, saved, expected[change["from"]]))
                    staged.add(change["from"])
                else:
                    handles[index] = held.enter_context(self._proof(fs, preview / change["from"], expected[change["from"]], rename=True))
            names = sorted(entry.name for entry in preview.iterdir())
            if names != sorted(set(item["snapshot"]["names"]) - staged): raise LocalFilesError("PREVIEW_SOURCE_CHANGED")
            for name, proof in expected.items():
                if name not in selected: held.enter_context(self._proof(fs, preview / name, proof))
            for index, handle in handles.items():
                fs.rename_handle(handle, quarantine / f"{index:04d}.png")

    def _finish(self, key, item, expected, preview, quarantine):
        fs = self.library.fs; journal = self.library.journal
        selected = {change["from"] for change in item["changes"]}
        expected_names = set(item["snapshot"]["names"]) - selected
        handles = {}
        with ExitStack() as held:
            for index, change in enumerate(item["changes"]):
                saved = quarantine / f"{index:04d}.png"
                proof = expected[change["from"]]
                if saved.exists():
                    handle = held.enter_context(self._proof(journal, saved, proof, rename=change["to"] is not None))
                    if change["to"] is not None: handles[index] = handle
                elif change["to"] is not None:
                    held.enter_context(self._proof(fs, preview / change["to"], proof))
                    expected_names.add(change["to"])
                else: raise LocalFilesError("PREVIEW_QUARANTINE_CHANGED")
            if sorted(entry.name for entry in preview.iterdir()) != sorted(expected_names): raise LocalFilesError("PREVIEW_SOURCE_CHANGED")
            for name, proof in expected.items():
                if name not in selected: held.enter_context(self._proof(fs, preview / name, proof))
            for index, handle in handles.items():
                journal.rename_handle(handle, preview / item["changes"][index]["to"])
