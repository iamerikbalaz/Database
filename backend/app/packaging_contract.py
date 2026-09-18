"""Independent validation of private packaging requests and retained proofs."""
from datetime import datetime
import hashlib
import json
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.material_review import canonical_hash
from app.technical_client import TechnicalReport

Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Version = Annotated[int, Field(ge=1, le=1)]
Size = Annotated[int, Field(ge=0, le=128 * 1024**3)]
Side = Annotated[int, Field(ge=1, le=32768)]
Bits = Literal[1, 2, 4, 8, 16]
Map = Literal["AO", "COL", "DISP16", "DISP", "GLOSS", "NRM16", "NRM", "ROUGH"]
Format = Literal["PNG", "JPEG", "TIFF", "WEBP"]
Policy = Literal["LEGACY_BEFORE_2026_03_04", "CURRENT_ON_OR_AFTER_2026_03_04"]
Operation = Annotated[str, Field(pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$")]
Path = Annotated[str, Field(min_length=1, max_length=4096)]
CONVERSION_POLICY_SHA256 = "4f01e6391b3c53642c6d813fc9f350a4a277f4767385ad4b4ed5a8438a50a0a4"


def _require(condition):
    if not condition: raise ValueError("Invalid packaging contract")


def _path(path, *, directory=False):
    value = path[:-1] if directory and path.endswith("/") else path
    parts = value.split("/")
    _require(len(value.encode("utf-8")) <= 4096 and all(part not in {"", ".", ".."} and len(part.encode("utf-8")) <= 255
        and not any(ord(char) < 32 or ord(char) == 127 or char in "\\:" for char in part) for part in parts))


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PackagingLimits(Strict):
    seconds: Annotated[int, Field(ge=1, le=3600)] = 1800
    staged_bytes: Annotated[int, Field(ge=1, le=256 * 1024**3)] = 8 * 1024**3
    generated_bytes: Annotated[int, Field(ge=1, le=128 * 1024**3)] = 16 * 1024**3
    retained_bytes: Annotated[int, Field(ge=1, le=128 * 1024**3)] = 16 * 1024**3


class PackagingRequest(Strict):
    version: Version
    operation_id: Operation
    parts: Annotated[list[Annotated[str, Field(min_length=1, max_length=255)]], Field(min_length=1, max_length=16)]
    source_revision_hash: Sha
    technical_report_hash: Sha
    approval_context_hash: Sha
    policy: Policy
    storage_timezone: Annotated[str, Field(min_length=1, max_length=100)]
    plan_hash: Sha
    limits: PackagingLimits

    @model_validator(mode="after")
    def check(self) -> Self:
        for part in self.parts: _path(part); _require("/" not in part)
        ZoneInfo(self.storage_timezone)
        return self

    def verify_report(self, raw):
        report = TechnicalReport.model_validate_json(json.dumps(raw, allow_nan=False))
        _require(report.can_approve and not report.errors and report.inventory.folder_name == self.parts[-1]
            and report.inventory.source_revision_hash == self.source_revision_hash
            and canonical_hash({key: value for key, value in raw.items() if key != "inventory"}) == self.technical_report_hash)
        _require(1 <= int(report.inventory.master_resolution[:-1]) <= 32)
        return report


class PreparedPackaging(Strict):
    request: PackagingRequest
    request_hash: Sha

    @model_validator(mode="after")
    def check(self) -> Self:
        _require(canonical_hash(self.request.model_dump(mode="json")) == self.request_hash)
        return self

    def verify_preparation(self, input):
        self.request.verify_report(input["report"])
        expected = {"version": 1, "operation_id": input["operation_id"], "parts": input["parts"],
            "source_revision_hash": input["expected_source_revision_hash"], "technical_report_hash": input["expected_technical_report_hash"],
            "approval_context_hash": input["approval_context_hash"], "policy": input["policy"], "storage_timezone": input["storage_timezone"],
            "limits": PackagingLimits.model_validate(input.get("limits", {})).model_dump()}
        _require(self.request.model_dump(mode="json", exclude={"plan_hash"}) == expected)


class Conversion(Strict):
    sha256: Sha
    size: Annotated[int, Field(ge=1, le=2 * 1024**3)]
    width: Side
    height: Side
    bits: Bits
    format: Format
    input_sha256: Sha
    runtime_version: Annotated[str, Field(pattern=r"^7\.[0-9.]+(?:-[0-9]+)?$", max_length=50)]
    policy_sha256: Literal[CONVERSION_POLICY_SHA256]


class MapOperation(Strict):
    source: Path
    destination: Path
    source_sha256: Sha
    shortcut: Map
    format: Format
    bits: Bits
    width: Side
    height: Side
    action: Literal["COPY", "RESIZE"]
    input_is_effective_master: bool


class MapProof(Strict):
    operation: MapOperation
    input_sha256: Sha
    size: Annotated[int, Field(ge=1, le=64 * 1024**3)]
    sha256: Sha
    bits: Bits
    conversion: Conversion | None


class ArchiveEntry(Strict):
    path: Path
    size: Size
    sha256: Sha | None
    crc32: Annotated[int, Field(ge=0, le=2**32 - 1)]
    date_time: Annotated[list[int], Field(min_length=6, max_length=6)]

    @model_validator(mode="after")
    def check(self) -> Self:
        _path(self.path, directory=True)
        datetime(*self.date_time)
        _require(1980 <= self.date_time[0] <= 2107 and self.date_time[-1] % 2 == 0)
        if self.path.endswith("/"): _require(self.size == 0 and self.sha256 is None and self.crc32 == 0)
        else: _require(self.sha256 is not None)
        return self


class Archive(Strict):
    filename: Path
    size: Annotated[int, Field(ge=1, le=8 * 1024**3)]
    sha256: Sha
    policy: Policy
    storage_timezone: Annotated[str, Field(min_length=1, max_length=100)]
    entries: Annotated[list[ArchiveEntry], Field(min_length=1, max_length=20001)]


class Bundle(Strict):
    operation_id: Operation
    plan_sha256: Sha
    storage_timezone: Annotated[str, Field(min_length=1, max_length=100)]
    manifest_sha256: Sha
    maps: Annotated[list[MapProof], Field(min_length=1, max_length=48)]
    archives: Annotated[list[Archive], Field(min_length=1, max_length=6)]


class RetainedFile(Strict):
    path: Path
    size: Annotated[int, Field(ge=0, le=16 * 1024**3)]
    sha256: Sha


class Payload(Strict):
    schema_version: Version
    bundle: Bundle
    bundle_sha256: Sha
    files: Annotated[list[RetainedFile], Field(min_length=2, max_length=20008)]
    directories: Annotated[list[Path], Field(max_length=20000)]

    @model_validator(mode="after")
    def check(self) -> Self:
        _require(canonical_hash(self.bundle.model_dump(mode="json")) == self.bundle_sha256)
        paths = [item.path for item in self.files]
        _require(paths == sorted(set(paths)) and self.directories == sorted(set(self.directories)))
        names = [*paths, *(path.removesuffix("/") for path in self.directories)]
        _require(len({path.casefold() for path in names}) == len(names))
        for path in names: _path(path)
        _require(all(path.endswith("/") for path in self.directories))
        _require(sum(item.size for item in self.files) <= 128 * 1024**3)
        return self


class RetentionAttempt(Strict):
    attempt: Annotated[int, Field(ge=1, le=32)]
    proof_sha256: Sha
    outcome: Literal["INCOMPLETE"]


class StoredPackaging(Strict):
    plan_hash: Sha
    proof_sha256: Sha
    attempt: Annotated[int, Field(ge=1, le=32)]
    attempt_history: Annotated[list[RetentionAttempt], Field(max_length=31)]
    payload: Payload

    @model_validator(mode="after")
    def check(self) -> Self:
        _require(canonical_hash(self.payload.model_dump(mode="json")) == self.proof_sha256
            and self.plan_hash == self.payload.bundle.plan_sha256
            and [item.attempt for item in self.attempt_history] == list(range(1, self.attempt)))
        return self


class PackagingResult(Strict):
    version: Version
    operation_id: Operation
    request_hash: Sha
    status: Literal["READY", "RETRY_REQUIRED"]
    attempt: Annotated[int, Field(ge=1, le=32)]
    stored: StoredPackaging | None

    @model_validator(mode="after")
    def check(self) -> Self:
        _require((self.status == "READY") == (self.stored is not None))
        return self

    def verify_request(self, prepared: PreparedPackaging, raw_report: dict):
        request = prepared.request; report = request.verify_report(raw_report)
        _require(self.operation_id == request.operation_id and self.request_hash == prepared.request_hash)
        if self.stored is None: return
        stored = self.stored; payload = stored.payload; bundle = payload.bundle
        _require(stored.attempt <= self.attempt and stored.plan_hash == request.plan_hash
            and bundle.operation_id == request.operation_id and bundle.storage_timezone == request.storage_timezone
            and sum(item.size for item in payload.files) <= request.limits.retained_bytes)
        _verify_layout(request, report, payload)


def _fit(width, height, side):
    return tuple(max(1, (value * side * 2 + max(width, height)) // (max(width, height) * 2)) for value in (width, height))


def _verify_layout(request, report, payload):
    inventory = report.inventory; identity = inventory.folder_name
    source_files = {entry.path: entry for entry in inventory.entries if entry.kind == "file"}
    images = sorted(report.images, key=lambda item: item.map)
    color = next(item for item in images if item.map == "COL")
    effective = min(int(inventory.master_resolution[:-1]), max(color.width, color.height) // 1024)
    numbers = [effective, *(number for number in (16, 8, 4, 2, 1) if number < effective)]
    dimensions = [_fit(*_fit(color.width, color.height, effective * 1024), number * 1024) for number in numbers]
    bundle = payload.bundle
    _require(len(bundle.archives) == len(numbers) and len(bundle.maps) == len(images) * len(numbers))
    preview_files = {path: item for path, item in source_files.items() if path.startswith("PREVIEW/")}
    required_sources = {image.path for image in images} | set(preview_files)
    if "metadata.txt" in source_files: required_sources.add("metadata.txt")
    _require(sum(source_files[path].size for path in required_sources) <= request.limits.staged_bytes)
    preview_dirs = sorted(entry.path + "/" for entry in inventory.entries if entry.kind == "directory"
        and (entry.path == "PREVIEW" or entry.path.startswith("PREVIEW/")))
    _require(payload.directories == preview_dirs)
    manifest = (json.dumps({"WEB_APP_PART": {
        "TEXTURE_RESOLUTIONS": {str(number) + "K": str(width) + "x" + str(height) for number, (width, height) in zip(numbers, dimensions, strict=True)},
        "IMAGE_RATIO": float(format(dimensions[0][1] / dimensions[0][0], ".6g")), "MAPS_SHORTCUTS": [image.map for image in images]},
        "DESKTOP_APP_PART": {}}, ensure_ascii=True, indent=2, allow_nan=False) + "\n").encode()
    manifest_hash = hashlib.sha256(manifest).hexdigest()
    _require(bundle.manifest_sha256 == manifest_hash)
    expected_retained = {"metadata.json": (len(manifest), manifest_hash), **{path: (item.size, item.sha256) for path, item in preview_files.items()}}
    generated = len(manifest); master = {}
    for index, (number, (width, height), archive) in enumerate(zip(numbers, dimensions, bundle.archives, strict=True)):
        resolution = str(number) + "K"; prefix = identity + "_" + resolution; root = prefix + "/"
        _require(archive.filename == prefix + ".zip" and archive.policy == request.policy and archive.storage_timezone == request.storage_timezone)
        expected_retained[archive.filename] = (archive.size, archive.sha256); generated += archive.size
        expected = {root: (0, None), root + resolution + "/": (0, None), root + "metadata.json": (len(manifest), manifest_hash)}
        expected.update({root + path: (0, None) for path in preview_dirs})
        expected.update({root + path: (item.size, item.sha256) for path, item in preview_files.items()})
        metadata = source_files.get("metadata.txt")
        if metadata: expected[root + resolution + "/metadata.txt"] = (metadata.size, metadata.sha256)
        for offset, image in enumerate(images):
            item = bundle.maps[index * len(images) + offset]; operation = item.operation
            extension = image.path.rsplit(".", 1)[-1]
            destination = resolution + "/" + identity + "_" + image.map + "_" + resolution + "." + extension
            action = "COPY" if index == 0 and color.width == color.height == effective * 1024 else "RESIZE"
            _require(operation.model_dump() == {"source": image.path, "destination": destination, "source_sha256": image.sha256,
                "shortcut": image.map, "format": image.format, "bits": image.bits, "width": width, "height": height,
                "action": action, "input_is_effective_master": index > 0})
            expected_input = image.sha256 if index == 0 else master[image.map]
            _require(item.input_sha256 == expected_input and item.bits == image.bits)
            if action == "COPY":
                _require(item.conversion is None and item.sha256 == image.sha256 and item.size == source_files[image.path].size)
            else:
                proof = item.conversion
                _require(proof is not None and (proof.sha256, proof.size, proof.width, proof.height, proof.bits, proof.format, proof.input_sha256)
                    == (item.sha256, item.size, width, height, image.bits, image.format, expected_input))
            if index == 0: master[image.map] = item.sha256
            expected[root + destination] = (item.size, item.sha256); generated += item.size
        paths = [entry.path for entry in archive.entries]
        _require(paths == sorted(expected) and {entry.path: (entry.size, entry.sha256) for entry in archive.entries} == expected)
        _require(sum(entry.size for entry in archive.entries) <= 16 * 1024**3)
        if request.policy == "LEGACY_BEFORE_2026_03_04":
            _require(all(entry.date_time == [2026, 1, 1, 0, 0, 0] for entry in archive.entries))
    _require({item.path: (item.size, item.sha256) for item in payload.files} == expected_retained
        and generated <= request.limits.generated_bytes)
