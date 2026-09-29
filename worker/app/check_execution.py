"""Private automatic-check snapshots and authenticated, content-bound evidence.

This module never supplies a packaging proof. Cache failure is a miss; source
hashing and the final original-source verification are mandatory even on hits.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from app.image_probe import MAX_PIXELS, MAX_SIDE
from app.inventory import AUTOMATIC_FILES_SCOPE, InventoryLimits, inventory_material
from app.technical_validation import probe_image

STAGING_BYTES = 8 * 1024**3  # per material, at most two isolated material workers
CACHE_RECORD_BYTES = 4096
CACHE_BUCKETS = 100
CACHE_BUCKET_RECORDS = 100
CACHE_MAX_RECORDS = CACHE_BUCKETS * CACHE_BUCKET_RECORDS
CACHE_MAX_BYTES = CACHE_MAX_RECORDS * CACHE_RECORD_BYTES
EVIDENCE_VERSION = "PBR_FILES_V1:image-probe-2"
SHA = re.compile(r"[a-f0-9]{64}\Z")
PHASES = frozenset({"STAGING", "CHECKING", "VERIFYING", "CACHED", "QUEUED", "HASHING", "DECODING"})


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def valid_evidence(value, digest):
    return (isinstance(value, dict) and set(value) == {"width", "height", "bits", "format", "sha256", "mode"}
        and value["sha256"] == digest and isinstance(digest, str) and SHA.fullmatch(digest) is not None
        and all(type(value[field]) is int and value[field] > 0 for field in ("width", "height", "bits"))
        and value["width"] <= MAX_SIDE and value["height"] <= MAX_SIDE
        and value["width"] * value["height"] <= MAX_PIXELS and value["bits"] in {1, 2, 4, 8, 16}
        and value["format"] in {"PNG", "JPEG", "TIFF", "WEBP"}
        and value["mode"] in {"1", "L", "LA", "P", "RGB", "RGBA", "I;16", "I;16B", "I;16L"})


def read_regular(path, limit, *, directory_fd=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise ValueError("CACHE_INVALID")
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(fd, min(4096, limit + 1 - len(data)))
            if not chunk: break
            data.extend(chunk)
        after = os.fstat(fd)
        if len(data) > limit or (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("CACHE_INVALID")
        return bytes(data)
    finally:
        os.close(fd)


class EvidenceCache:
    def __init__(self, root: Path, key_path: Path, namespace: str):
        self.root = root
        self.namespace = namespace
        self.key = None
        try:
            if not isinstance(namespace, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", namespace): return
            key = read_regular(key_path, 32)
            if len(key) != 32: return
            self.key = key
        except (OSError, ValueError):
            pass

    def name(self, digest):
        return hashlib.sha256(canonical([self.namespace, EVIDENCE_VERSION, digest])).hexdigest() + ".json"

    def bucket(self, digest):
        return f"{int(self.name(digest)[:8], 16) % CACHE_BUCKETS:02d}"

    @contextmanager
    def directory(self, digest, *, create=False):
        root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            bucket = self.bucket(digest)
            if create:
                try: os.mkdir(bucket, mode=0o700, dir_fd=root_fd)
                except FileExistsError: pass
            fd = os.open(bucket, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            try: yield fd
            finally: os.close(fd)
        finally: os.close(root_fd)

    def get(self, digest):
        if self.key is None or not isinstance(digest, str) or not SHA.fullmatch(digest): return None
        try:
            with self.directory(digest) as directory:
                record = json.loads(read_regular(self.name(digest), CACHE_RECORD_BYTES, directory_fd=directory))
            if not isinstance(record, dict) or set(record) != {"schema_version", "namespace", "version", "sha256", "evidence", "signature"}: return None
            signature = record.pop("signature")
            if (type(record["schema_version"]) is not int or record["schema_version"] != 1
                    or record["namespace"] != self.namespace or record["version"] != EVIDENCE_VERSION
                    or record["sha256"] != digest or not valid_evidence(record["evidence"], digest)
                    or not isinstance(signature, str) or not SHA.fullmatch(signature)):
                return None
            if not hmac.compare_digest(signature, hmac.new(self.key, canonical(record), hashlib.sha256).hexdigest()): return None
            return dict(record["evidence"])
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            return None

    def put(self, digest, evidence):
        if self.key is None or not valid_evidence(evidence, digest): return
        record = {"schema_version": 1, "namespace": self.namespace, "version": EVIDENCE_VERSION,
            "sha256": digest, "evidence": evidence}
        record["signature"] = hmac.new(self.key, canonical(record), hashlib.sha256).hexdigest()
        content = canonical(record)
        if len(content) > CACHE_RECORD_BYTES: return
        # Fixed hash buckets bound the scan to 100 records, instead of an O(N)
        # scan of the entire Windows-mounted cache on every stored image. Each
        # bucket is separately locked; saturation simply disables new stores.
        try:
            import fcntl
            from uuid import uuid4
            with self.directory(digest, create=True) as directory:
                lock = os.open(".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
                try:
                    info = os.fstat(lock)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: return
                    # Cache contention or an untrusted held lock must never
                    # delay a material check. Losing a store is a safe miss.
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    name = self.name(digest)
                    count = 0; size = 0
                    with os.scandir(directory) as listing:
                        for item in listing:
                            if item.name == ".lock": continue
                            count += 1
                            if count > CACHE_BUCKET_RECORDS: return
                            info = item.stat(follow_symlinks=False)
                            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                                    or info.st_size > CACHE_RECORD_BYTES): return
                            if item.name == name:
                                count -= 1  # replacing a corrupt, authenticated-key entry is safe
                                continue
                            size += info.st_size
                    if count >= CACHE_BUCKET_RECORDS or size + len(content) > CACHE_BUCKET_RECORDS * CACHE_RECORD_BYTES: return
                    temporary = ".write-" + uuid4().hex
                    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                    try:
                        pending = memoryview(content)
                        while pending:
                            written = os.write(fd, pending)
                            if written <= 0: raise OSError("cache write failed")
                            pending = pending[written:]
                        os.fsync(fd)
                        os.close(fd); fd = None
                        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                    finally:
                        if fd is not None: os.close(fd)
                        try: os.unlink(temporary, dir_fd=directory)
                        except FileNotFoundError: pass
                finally: os.close(lock)
        except (OSError, ValueError, TypeError):
            pass


@dataclass(frozen=True)
class ExecutionOptions:
    staging_root: Path
    cache_root: Path
    key_path: Path
    namespace: str


class MaterialExecution:
    def __init__(self, options: ExecutionOptions, progress=None):
        self.options = options
        self.progress = progress
        self.hits = 0; self.misses = 0
        self.cache = EvidenceCache(options.cache_root, options.key_path, options.namespace)

    def emit(self, phase, path=""):
        if self.progress is not None:
            self.progress({"phase": phase, "file": path, "cache_hits": self.hits, "cache_misses": self.misses})

    @contextmanager
    def snapshot(self, root, parts):
        # A fresh private Linux-volume directory per material; no user paths
        # choose destinations. TemporaryDirectory removes only this owned tree.
        info = self.options.staging_root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
            raise ValueError("FILE_CHECK_STAGING_UNAVAILABLE")
        with tempfile.TemporaryDirectory(prefix="material-", dir=self.options.staging_root) as private:
            snapshot_root = Path(private)
            destination = snapshot_root.joinpath(*parts)
            destination.mkdir(mode=0o700, parents=True)
            self.emit("HASHING")
            inventory = inventory_material(root, parts, scope=AUTOMATIC_FILES_SCOPE,
                limits=InventoryLimits(max_total_bytes=STAGING_BYTES, max_file_bytes=STAGING_BYTES),
                copy_to=destination, progress=lambda path: self.emit("STAGING", path))
            yield snapshot_root, inventory

    def probe(self, fd, entry, *, timeout, include_mode=False, wall_limit=35):
        digest = entry["sha256"]
        value = self.cache.get(digest)
        if value is not None:
            self.hits += 1
            self.emit("CACHED", entry["path"])
        else:
            self.misses += 1
            self.emit("DECODING", entry["path"])
            value = probe_image(fd, timeout=timeout, include_mode=True, wall_limit=wall_limit)
            if "error" not in value and value["sha256"] == digest:
                self.cache.put(digest, value)
        if not include_mode and "mode" in value:
            value = {key: item for key, item in value.items() if key != "mode"}
        return value
