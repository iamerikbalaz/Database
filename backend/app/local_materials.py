"""Local desktop library services, enabled only by an explicit application adapter."""
import base64
from contextlib import ExitStack
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import stat
import subprocess
from threading import BoundedSemaphore, Lock, RLock
from uuid import UUID, uuid4

from app.discovery_client import DiscoveryClientError, FolderDiscovery
from app.folder_contents import FolderContents
from app.identity_client import IdentityClientError, IdentityPlan, IdentityResult
from app.local_filesystem import LocalFilesystem, LocalFilesError
from app.material_naming import base_name, match_identity
from app.material_review import canonical_hash
from app.metadata_client import MetadataClientError, MetadataObservation, MetadataResult, MetadataValues, SourceMetadata
from app.metadata_document import rewrite_metadata_json
from app.preview_client import PreviewClientError, PreviewListing, PreviewImage, PreviewOriginal, validate_preview_name


def digest(raw): return hashlib.sha256(raw).hexdigest()


def renamed(part, before, after):
    for old, new in ((before, after), (base_name(before), base_name(after))):
        if part == old or part.startswith((old + "_", old + ".")): return new + part[len(old):]
    return part


def source(raw, folder):
    from app.metadata_document import MAX_BYTES, _unique, _invalid
    values = {key: None for key in MetadataValues.model_fields}; status = "MISSING"
    text = None
    if raw is not None:
        if len(raw) > MAX_BYTES: raise ValueError("SOURCE_METADATA_TOO_LARGE")
        text = raw.decode("utf-8")
        try:
            data = json.loads(text, parse_float=Decimal, parse_int=Decimal,
                object_pairs_hook=_unique, parse_constant=_invalid)
            if not isinstance(data, dict) or any(key in data for key in ("WEB_APP_PART", "DESKTOP_APP_PART")): raise ValueError()
            color = data.get("COLOR", {}); size = data.get("TEXTURE_SIZE", {})
            if not isinstance(color, dict) or not isinstance(size, dict): raise ValueError()
            dimensions = size.get("cm", {})
            if not isinstance(dimensions, dict): raise ValueError()
            if any(dimensions.get(name) is not None and not isinstance(dimensions[name], Decimal) for name in ("width", "height")):
                raise ValueError()
            values = MetadataValues(hex_color=color.get("hex"),
                width_cm=str(dimensions["width"]) if dimensions.get("width") is not None else None,
                height_cm=str(dimensions["height"]) if dimensions.get("height") is not None else None).model_dump()
            status = "VALID" if all(value is not None for value in values.values()) else "WARNING"
        except (ValueError, AttributeError, TypeError, RecursionError): status = "INVALID"
    return SourceMetadata(schema_version=1, source_filename="metadata.json", folder_name=folder, status=status,
        sha256=digest(raw) if raw is not None else None, raw_content=text, **values)


class LocalMaterialLibrary:
    def __init__(self, root, journal_root, *, file_checker=None):
        self.fs = LocalFilesystem(root)
        journal = Path(os.path.abspath(journal_root))
        if journal.is_relative_to(self.fs.root) or self.fs.root.is_relative_to(journal): raise LocalFilesError("LOCAL_JOURNAL_OVERLAP")
        parent = LocalFilesystem(journal.parent)
        with parent.directory(""):
            journal.mkdir(exist_ok=True)
        self.journal = LocalFilesystem(journal)
        with self.fs.directory(""), self.journal.directory(""), self.fs.opened(self.fs.root, directory=True) as source_handle, self.journal.opened(journal, directory=True) as journal_handle:
            if self.fs.identity(source_handle)[0] != self.journal.identity(journal_handle)[0]:
                raise LocalFilesError("LOCAL_JOURNAL_CROSS_VOLUME")
        self.lock = RLock(); self.picker_lock = Lock()
        self.previews = LocalPreviews(self); self.metadata = LocalMetadata(self); self.identity = LocalIdentity(self)
        self.file_checker = file_checker

    @property
    def file_check_profile(self):
        return self.file_checker.profile if self.file_checker is not None else "BASIC_V1"

    def preflight(self, folder):
        from app.local_preflight import LocalMaterialPreflight
        return LocalMaterialPreflight(self).preflight(folder)

    def check_many(self, folders, *, progress=None):
        if self.file_checker is not None:
            return self.file_checker.check_many(folders, **({"progress": progress} if progress is not None else {}))
        return [self.check(folder) for folder in folders]

    def absolute(self, folder):
        with self.fs.directory(folder) as path: return str(path)

    def listing(self, parent):
        try:
            with self.lock, self.fs.directory(parent) as path:
                entries = []; omitted = 0
                with os.scandir(path) as iterator:
                    for entry in iterator:
                        if len(entries) + omitted >= 4096: raise LocalFilesError("LOCAL_ENTRY_LIMIT")
                        # Windows DirEntry.stat may report st_nlink=0 from the
                        # directory enumeration cache; query the actual file.
                        info = os.stat(entry.path, follow_symlinks=False)
                        relative = parent + "/" + entry.name if parent else entry.name
                        try: self.fs.path(relative)
                        except ValueError: omitted += 1; continue
                        if (getattr(info, "st_file_attributes", 0) & 0x400 or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                                or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)):
                            omitted += 1; continue
                        entries.append({"name": entry.name, "path": relative, "kind": "directory" if entry.is_dir(follow_symlinks=False) else "file",
                                        "size": 0 if entry.is_dir(follow_symlinks=False) else info.st_size})
                return FolderContents(schema_version=1, parent_path=parent, entries=sorted(entries, key=lambda x:(x["kind"] != "directory", x["name"])), omitted_entries=omitted)
        except (OSError, ValueError, LocalFilesError): raise DiscoveryClientError("DISCOVERY_READ_FAILED") from None

    def discovery(self, parent):
        listing = self.listing(parent)
        return FolderDiscovery(schema_version=1, parent_path=parent,
            directories=[{"name": e.name, "path": e.path} for e in listing.entries if e.kind == "directory"],
            omitted_entries=listing.omitted_entries + sum(e.kind == "file" for e in listing.entries))

    def open_folder(self, folder):
        with self.lock, self.fs.directory(folder) as path:
            subprocess.Popen([str(Path(os.environ["WINDIR"]) / "explorer.exe"), str(path)], close_fds=True)

    def open_metadata(self, folder):
        with self.lock, self.fs.directory(folder) as path, self.fs.opened(path / "metadata.json"):
            # A fixed text editor cannot interpret the metadata path as a command.
            subprocess.Popen([str(Path(os.environ["WINDIR"]) / "System32" / "notepad.exe"), str(path / "metadata.json")], close_fds=True)

    def select_destination(self):
        value = self.select_settings_folder(str(self.fs.root), "Select destination brand folder inside Test_data")
        if value is None: return {"destination_path": None, "target_parent": None}
        parent = self.fs.relative(value)
        with self.fs.directory(parent) as path: return {"destination_path": str(path), "target_parent": parent}

    def select_settings_folder(self, initial_path, description):
        from app.native_folder_picker import select_native_directory
        if not self.picker_lock.acquire(False): raise LocalFilesError("LOCAL_PICKER_BUSY")
        try:
            return select_native_directory(description, initial_path)
        finally: self.picker_lock.release()

    def check(self, folder, *, progress=None):
        if self.file_checker is not None:
            return self.file_checker.check(folder, **({"progress": progress} if progress is not None else {}))
        # Pin all existing source files without hashing large texture payloads.
        # These read handles prevent writes/renames during the observation.
        with self.lock, self.fs.tree(folder, hash_files=False, for_rename=False) as (_, before, _):
            count = 0; size = 0; issues = []; pending = [folder]
            while pending:
                listed = self.listing(pending.pop())
                if listed.omitted_entries: issues.append(f"{listed.omitted_entries} unsafe or unsupported entries were excluded.")
                for entry in listed.entries:
                    count += 1
                    if count > 4096: raise LocalFilesError("LOCAL_ENTRY_LIMIT")
                    if entry.kind == "directory": pending.append(entry.path)
                    else: size += entry.size
            metadata = self.metadata.inspect(folder)
            if metadata.status != "VALID": issues.append("metadata.json: " + metadata.status)
            previews = self.previews.listing(folder)
            if not previews.items: issues.append("No supported previews in PREVIEW.")
            with self.fs.tree(folder, hash_files=False, for_rename=False) as (_, after, _):
                if before != after: raise LocalFilesError("LOCAL_SOURCE_CHANGED")
            report = "\n".join(["Basic material data check", datetime.now(timezone.utc).isoformat(),
                "Folder: " + self.absolute(folder), f"Entries: {count}; file bytes: {size}",
                f"metadata.json: {metadata.status}", f"Preview images: {len(previews.items)}", "",
                "Issues:", *(issues or ["No issues in the basic checks."]), "",
                "These checks inspect the folder tree, metadata and preview availability. Final map/ZIP validation rules are not configured yet."])
            return {"report": report, "issues": issues}

    def save_check_report(self, report, *, open_report=True):
        from app.local_check_reports import LocalCheckReports
        reports = LocalCheckReports()
        if reports.root.is_relative_to(self.fs.root) or self.fs.root.is_relative_to(reports.root):
            raise LocalFilesError("LOCAL_REPORT_SOURCE_OVERLAP")
        return reports.save(report, open_report=open_report)

    def journal_state(self, operation_id, request):
        key = str(UUID(operation_id)); directory = self.journal.root / key
        with self.journal.directory(""):
            directory.mkdir(exist_ok=True)
        request_hash = canonical_hash(request)
        with self.journal.directory(key):
            state_path = directory / "state.json"
            if state_path.exists():
                state = json.loads(self.journal.read(key + "/state.json"))
                if state["request_hash"] != request_hash: raise LocalFilesError("LOCAL_REQUEST_CONFLICT")
                return key, state
            return key, {"request_hash": request_hash}

    def write_state(self, key, state):
        with self.journal.directory(key) as path:
            temporary = path / ("state-" + uuid4().hex + ".next")
            with open(temporary, "xb") as output:
                output.write(json.dumps(state, ensure_ascii=True, sort_keys=True).encode()); output.flush(); os.fsync(output.fileno())
            os.replace(temporary, path / "state.json")


class LocalMetadata:
    def __init__(self, library): self.library = library

    def inspect(self, folder):
        try:
            with self.library.lock:
                try: raw = self.library.fs.read(folder + "/metadata.json")
                except FileNotFoundError: raw = None
                value = source(raw, folder.rsplit("/", 1)[-1])
                return MetadataObservation(**value.model_dump(), editable=value.status != "INVALID", writes_enabled=True)
        except (OSError, ValueError, LocalFilesError): raise MetadataClientError() from None

    def execute(self, request):
        library = self.library
        try:
            if request.get("source_filename") != "metadata.json": raise LocalFilesError("LOCAL_LEGACY_OPERATION_UNSUPPORTED")
            values = MetadataValues(**request["values"]).model_dump()
            with library.lock:
                key, state = library.journal_state(request["operation_id"], request)
                if "result" in state: return MetadataResult.model_validate(state["result"])
                folder = request["folder_path"]
                with library.fs.directory(folder) as directory, library.journal.directory(key) as journal:
                    current = self.inspect(folder)
                    if "after" not in state:
                        if current.sha256 != request["expected_sha256"]:
                            result = {"operation_id": request["operation_id"], "status": "REJECTED", "failure_code": "METADATA_SOURCE_CHANGED", "metadata": None}
                            state["result"] = result; library.write_state(key, state); return MetadataResult.model_validate(result)
                        # File facts use a complete handle-verified tree. Texture
                        # bytes need not be hashed to describe their filenames.
                        # Release these handles before the atomic metadata swap.
                        with library.fs.tree(folder, hash_files=False, for_rename=False) as (_, inventory, _):
                            raw = rewrite_metadata_json(current.raw_content.encode() if current.raw_content is not None else None,
                                values, request.get("identity"), inventory=inventory)
                        state.update(before=current.sha256, after=base64.b64encode(raw).decode(), after_hash=digest(raw))
                        temporary = journal / "new.json"
                        if temporary.exists():
                            if library.journal.read(key + "/new.json") != raw:
                                raise LocalFilesError("LOCAL_RECOVERY_REQUIRED")
                        else:
                            with open(temporary, "xb") as output:
                                output.write(raw); output.flush(); os.fsync(output.fileno())
                        library.write_state(key, state)
                    raw = base64.b64decode(state["after"])
                    if current.sha256 != state["after_hash"]:
                        if current.sha256 is not None:
                            if current.sha256 != state["before"]: raise LocalFilesError("LOCAL_METADATA_CHANGED")
                            with library.fs.opened(directory / "metadata.json", rename=True) as handle:
                                if digest(library.fs.read_handle(handle, limit=4*1024**2)) != state["before"]: raise LocalFilesError("LOCAL_METADATA_CHANGED")
                                library.fs.rename_handle(handle, journal / "before.json")
                        elif state["before"] is not None:
                            if digest(library.journal.read(key + "/before.json")) != state["before"]: raise LocalFilesError("LOCAL_RECOVERY_REQUIRED")
                        with library.journal.opened(journal / "new.json", rename=True) as handle:
                            if digest(library.journal.read_handle(handle, limit=4*1024**2)) != state["after_hash"]: raise LocalFilesError("LOCAL_RECOVERY_REQUIRED")
                            library.fs.rename_handle(handle, directory / "metadata.json")
                    final = self.inspect(folder)
                    if final.sha256 != state["after_hash"]: raise LocalFilesError("LOCAL_RECOVERY_REQUIRED")
                    result = {"operation_id": request["operation_id"], "status": "COMPLETED", "failure_code": None,
                              "metadata": SourceMetadata(**{k:v for k,v in final.model_dump().items() if k in SourceMetadata.model_fields}).model_dump()}
                    state["result"] = result; library.write_state(key, state)
                    return MetadataResult.model_validate(result)
        except (OSError, ValueError, LocalFilesError, KeyError): raise MetadataClientError() from None


class LocalPreviews:
    def __init__(self, library):
        self.library = library; self.cache = {}; self.slots = BoundedSemaphore(2); self.cache_lock = Lock()

    def listing(self, folder):
        try:
            with self.library.lock:
                path = self.library.fs.path(folder + "/PREVIEW")
                if not path.exists(): return PreviewListing(schema_version=1, folder_name=folder.rsplit("/", 1)[-1], missing=True, ignored_entries=0, items=[])
                items=[]; ignored=0
                with self.library.fs.directory(folder + "/PREVIEW") as path:
                    entries = list(path.iterdir())
                    if len(entries)>512: raise LocalFilesError("LOCAL_ENTRY_LIMIT")
                    for entry in entries:
                        try: validate_preview_name(entry.name)
                        except ValueError: ignored+=1; continue
                        with self.library.fs.opened(entry) as handle:
                            raw = self.library.fs.read_handle(handle, limit=64*1024**2)
                            items.append({"name":entry.name, "size":len(raw), "sha256":digest(raw)})
                    return PreviewListing(schema_version=1, folder_name=folder.rsplit("/", 1)[-1], missing=False, ignored_entries=ignored, items=sorted(items,key=lambda x:x["name"]))
        except (OSError, ValueError, LocalFilesError): raise PreviewClientError("PREVIEW_READ_FAILED") from None

    def image(self, folder, name, expected_sha256, size=1024):
        if not self.slots.acquire(timeout=30): raise PreviewClientError("PREVIEW_BUSY")
        try: return self._image(folder, name, expected_sha256, size)
        finally: self.slots.release()

    def original(self, folder, name, expected_sha256):
        if not self.slots.acquire(timeout=30): raise PreviewClientError("PREVIEW_BUSY")
        try:
            validate_preview_name(name)
            # Keep the source and every ancestor bound through decoding. Windows
            # sharing flags block replacing/renaming the source while it is read.
            with self.library.fs.directory(folder + "/PREVIEW") as directory:
                with self.library.fs.opened(directory / name) as handle:
                    raw = self.library.fs.read_handle(handle, limit=64*1024**2)
                    return self._original_bytes(folder, name, expected_sha256, raw)
        except PreviewClientError: raise
        except (OSError, ValueError, LocalFilesError): raise PreviewClientError("PREVIEW_READ_FAILED") from None
        finally: self.slots.release()

    def _original_bytes(self, folder, name, expected_sha256, raw):
        from PIL import Image, ImageOps
        try:
            if digest(raw) != expected_sha256: raise PreviewClientError("PREVIEW_SOURCE_CHANGED")
            with Image.open(BytesIO(raw), formats=("PNG", "JPEG", "TIFF", "WEBP")) as image:
                width, height = image.size
                if width * height > 32 * 1024**2 or max(width, height) > 32768:
                    raise PreviewClientError("PREVIEW_PIXEL_LIMIT")
                if getattr(image, "n_frames", 1) != 1: raise PreviewClientError("PREVIEW_MULTIFRAME_UNSUPPORTED")
                if image.mode not in {"1", "L", "LA", "P", "RGB", "RGBA", "I;16", "I;16B", "I;16L"}:
                    raise PreviewClientError("PREVIEW_MODE_UNSUPPORTED")
                source_format = image.format
                expected_format = {"png":"PNG", "jpg":"JPEG", "jpeg":"JPEG", "tif":"TIFF", "tiff":"TIFF", "webp":"WEBP"}[name.rsplit(".", 1)[-1].lower()]
                if source_format != expected_format: raise PreviewClientError("PREVIEW_EXTENSION_MISMATCH")
                image.verify()
            with Image.open(BytesIO(raw), formats=("PNG", "JPEG", "TIFF", "WEBP")) as image:
                image.load()
                if source_format == "TIFF":
                    image = ImageOps.exif_transpose(image)
                    icc_profile = image.info.get("icc_profile")
                    if image.mode == "P": image = image.convert("RGBA")
                    if image.mode in {"I;16B", "I;16L"}: image = Image.frombytes("I;16", image.size, image.tobytes(), "raw", image.mode)
                    image = Image.frombytes(image.mode, image.size, image.tobytes())
                    output = BytesIO(); image.save(output, format="PNG", **({"icc_profile":icc_profile} if icc_profile else {}))
                    raw = output.getvalue(); width, height = image.size
                media_type = {"PNG":"image/png", "JPEG":"image/jpeg", "TIFF":"image/png", "WEBP":"image/webp"}[source_format]
            # Originals are only loaded on demand and are never added to the thumbnail cache.
            return PreviewOriginal(schema_version=1, folder_name=folder.rsplit("/",1)[-1], name=name,
                source_sha256=expected_sha256, source_format=source_format, width=width, height=height,
                media_type=media_type, sha256=digest(raw), data=base64.b64encode(raw).decode())
        except PreviewClientError: raise
        except (OSError, ValueError, LocalFilesError): raise PreviewClientError("PREVIEW_READ_FAILED") from None

    def _image(self, folder, name, expected_sha256, size=1024):
        from PIL import Image, ImageOps
        try:
            validate_preview_name(name)
            if size not in {256,512,1024}: raise ValueError()
            raw = self.library.fs.read(folder + "/PREVIEW/" + name, limit=64*1024**2)
            if digest(raw) != expected_sha256: raise PreviewClientError("PREVIEW_SOURCE_CHANGED")
            cache_key=(expected_sha256,size)
            if cache_key not in self.cache:
                with Image.open(BytesIO(raw)) as image:
                    width,height=image.size
                    if width*height>32_000_000 or getattr(image,"n_frames",1)!=1: raise ValueError()
                    source_format=image.format
                    image=ImageOps.exif_transpose(image); image.thumbnail((size,size))
                    if image.mode=="RGBA":
                        background=Image.new("RGB",image.size,"white"); background.paste(image,mask=image.getchannel("A")); image=background
                    else: image=image.convert("RGB")
                    output=BytesIO(); image.save(output,format="JPEG",quality=85)
                    data=output.getvalue()
                    cached={"source_format":source_format,"width":image.width,"height":image.height,
                        "original_width":width,"original_height":height,"media_type":"image/jpeg","sha256":digest(data),"data":base64.b64encode(data).decode()}
                    with self.cache_lock:
                        self.cache[cache_key]=cached
                        if len(self.cache)>160: self.cache.pop(next(iter(self.cache)))
            with self.cache_lock: cached=self.cache.get(cache_key)
            if cached is None: raise PreviewClientError("PREVIEW_BUSY")
            return PreviewImage(schema_version=1, folder_name=folder.rsplit("/",1)[-1],name=name,source_sha256=expected_sha256,**cached)
        except PreviewClientError: raise
        except (OSError, ValueError, LocalFilesError): raise PreviewClientError("PREVIEW_READ_FAILED") from None


class LocalIdentity:
    def __init__(self, library): self.library=library

    def _plan(self, request, entries, handles):
        before=request["folder_path"].rsplit("/",1)[-1]; after=request["target_path"].rsplit("/",1)[-1]
        target=match_identity(after)
        if target is None: raise ValueError()
        changes=[]; errors=[]; warnings=[]; metadata={"before_hash":None,"after_hash":None,"changed_fields":[]}; rewritten=None
        targets=set(); metadata_found=False
        for item in entries:
            mapped="/".join(renamed(part,before,after) for part in item["path"].split("/"))
            if mapped.casefold() in targets: errors.append({"code":"IDENTITY_TARGET_COLLISION","path":item["path"]})
            targets.add(mapped.casefold())
            if mapped!=item["path"]: changes.append({"kind":item["kind"],"source":item["path"],"target":mapped,"sha256":item["sha256"]})
            if item["path"]=="metadata.json":
                metadata_found=True
                raw=self.library.fs.read_handle(handles[item["path"]],limit=4*1024**2)
                from app.metadata_identity_document import MetadataIdentityError, rewrite_identity_metadata
                metadata["before_hash"]=digest(raw)
                try:
                    rewritten,fields=rewrite_identity_metadata(raw,before,new_identity=after,
                        brand_name=request["brand_name"],material_name=request["material_name"])
                    metadata.update(after_hash=digest(rewritten),changed_fields=sorted(fields))
                except MetadataIdentityError as exc:
                    errors.append({"code":str(exc),"path":"metadata.json"})
        if not metadata_found: warnings.append({"code":"SOURCE_METADATA_MISSING","path":"metadata.json"})
        targetpath=self.library.fs.path(request["target_path"])
        same_path = request["target_path"] == request["folder_path"]
        if targetpath.exists() and not same_path: errors.append({"code":"IDENTITY_TARGET_COLLISION","path":request["target_path"]})
        if not same_path and targetpath.is_relative_to(self.library.fs.path(request["folder_path"])): errors.append({"code":"IDENTITY_TARGET_INSIDE_SOURCE","path":request["target_path"]})
        plan={"schema_version":1,"planner_version":"identity-plan-1","source_path":request["folder_path"],"target_path":request["target_path"],
            "source_revision_hash":canonical_hash(entries),"changes":changes,"metadata":metadata,"errors":errors,"warnings":warnings,"ready":not errors}
        plan["plan_hash"]=canonical_hash({"plan":plan,"brand_name":request["brand_name"],"material_name":request["material_name"]})
        return IdentityPlan.model_validate(plan),rewritten

    def plan(self, request):
        try:
            with self.library.lock, self.library.fs.tree(request["folder_path"]) as (_,entries,handles):
                parent=request["target_path"].rpartition("/")[0]
                with self.library.fs.directory(parent): return self._plan(request,entries,handles)[0]
        except (OSError,ValueError,LocalFilesError): raise IdentityClientError("IDENTITY_SOURCE_UNSUPPORTED") from None

    def _step(self, step):
        library=self.library
        source_fs=library.fs if step["source_area"]=="library" else library.journal
        target_fs=library.fs if step["target_area"]=="library" else library.journal
        source_path=source_fs.path(step["source"]); target_path=target_fs.path(step["target"])
        def parent(fs,path):
            value=path.parent.relative_to(fs.root).as_posix()
            return fs.directory("" if value=="." else value)
        with parent(source_fs,source_path), parent(target_fs,target_path):
            # A crash may happen after the atomic rename but before the receipt.
            at_source=source_path.exists(); at_target=target_path.exists()
            if at_source==at_target: raise LocalFilesError("LOCAL_RECOVERY_REQUIRED")
            current=source_path if at_source else target_path
            fs=source_fs if at_source else target_fs
            with fs.opened(current,directory=step["directory"],rename=at_source) as handle:
                if fs.identity(handle)!=step["identity"]: raise LocalFilesError("LOCAL_ENTRY_CHANGED")
                if not step["directory"] and fs.read_handle(handle,limit=32*1024**3,digest_only=True)!=step["sha256"]:
                    raise LocalFilesError("LOCAL_ENTRY_CHANGED")
                if at_source: fs.rename_handle(handle,target_path)

    def execute(self, request):
        try:
            library=self.library
            with library.lock:
                key,state=library.journal_state(request["operation_id"],request)
                if "result" in state: return IdentityResult.model_validate_json(json.dumps(state["result"]))
                if "plan" not in state:
                    with library.fs.tree(request["folder_path"]) as (path,entries,handles), library.fs.directory(request["target_path"].rpartition("/")[0]):
                        plan,rewritten=self._plan(request,entries,handles)
                        if not plan.ready or plan.plan_hash!=request["expected_plan_hash"]:
                            result={"operation_id":request["operation_id"],"status":"REJECTED","plan_hash":request["expected_plan_hash"],
                                "source_path":request["folder_path"],"target_path":request["target_path"],"source_revision_hash":None,"target_revision_hash":None,"failure_code":"IDENTITY_PLAN_CHANGED"}
                            state["result"]=result; library.write_state(key,state); return IdentityResult.model_validate_json(json.dumps(result))
                        steps=[]; expected=[]
                        def step(source_area,src,target_area,dst,handle,is_dir,sha):
                            fs=library.fs if source_area=="library" else library.journal
                            return {"source_area":source_area,"source":src,"target_area":target_area,"target":dst,
                                    "identity":fs.identity(handle),"directory":is_dir,"sha256":sha}
                        old=path.name; new=library.fs.path(request["target_path"]).name
                        for item in sorted(entries,key=lambda x:(-x["path"].count("/"),x["kind"]=="directory",x["path"])):
                            component=Path(item["path"]); new_component=renamed(component.name,old,new)
                            if new_component!=component.name:
                                destination=(component.parent/new_component).as_posix()
                                steps.append(step("library",request["folder_path"]+"/"+item["path"],"library",request["folder_path"]+"/"+destination,
                                    handles[item["path"]],item["kind"]=="directory",item["sha256"]))
                        if rewritten is not None and plan.metadata.before_hash!=plan.metadata.after_hash:
                            with library.journal.directory(key) as journal:
                                temporary=journal/"identity-new.json"
                                if temporary.exists():
                                    if digest(library.journal.read(key+"/identity-new.json"))!=digest(rewritten): raise LocalFilesError("LOCAL_RECOVERY_REQUIRED")
                                else:
                                    with open(temporary,"xb") as output:
                                        output.write(rewritten); output.flush(); os.fsync(output.fileno())
                                steps.append(step("library",request["folder_path"]+"/metadata.json","journal",key+"/before.json",handles["metadata.json"],False,plan.metadata.before_hash))
                                with library.journal.opened(temporary) as handle:
                                    steps.append(step("journal",key+"/identity-new.json","library",request["folder_path"]+"/metadata.json",handle,False,digest(rewritten)))
                        if request["folder_path"] != request["target_path"]:
                            steps.append(step("library",request["folder_path"],"library",request["target_path"],handles[""],True,None))
                        for item in entries:
                            mapped={**item,"path":"/".join(renamed(part,old,new) for part in item["path"].split("/"))}
                            if item["path"]=="metadata.json" and rewritten is not None: mapped.update(size=len(rewritten),sha256=digest(rewritten))
                            expected.append(mapped)
                        state.update(plan=plan.model_dump(mode="json"),steps=steps,completed_steps=0,expected_entries=sorted(expected,key=lambda x:x["path"]))
                        library.write_state(key,state)
                for index in range(state["completed_steps"],len(state["steps"])):
                    self._step(state["steps"][index])
                    state["completed_steps"]=index+1; library.write_state(key,state)
                with library.fs.tree(request["target_path"]) as (_,after_entries,_):
                    if state["expected_entries"]!=after_entries: raise LocalFilesError("LOCAL_RECOVERY_REQUIRED")
                plan=state["plan"]
                result={"operation_id":request["operation_id"],"status":"COMPLETED","plan_hash":plan["plan_hash"],"source_path":request["folder_path"],
                    "target_path":request["target_path"],"source_revision_hash":plan["source_revision_hash"],"target_revision_hash":canonical_hash(after_entries),"failure_code":None}
                state["result"]=result; library.write_state(key,state)
                return IdentityResult.model_validate_json(json.dumps(result))
        except IdentityClientError: raise
        except (OSError,ValueError,LocalFilesError): raise IdentityClientError("IDENTITY_RECOVERY_REQUIRED") from None
