"""Compile an internal staging plan and an all-object completion manifest.

Pure functions: no authorization, IO or publication state transition. The
coordinator must load matching immutable batch/package records and check current
approvals before any dispatch. INTERNAL_STAGING_V1 is not an importer layout.
"""
from dataclasses import dataclass
import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.gcs_contract import GcsConfiguration, GcsObjectReceipt, GcsObjectSpec, Job, Sha, Strict
from app.material_review import canonical_hash
from app.packaging_contract import DispatchedPackagingResult, PreparedPackaging
from app.publication_csv import PublicationCsvRow, render_publication_csv

MAX_BATCH_FILES = 20001
MAX_BATCH_BYTES = 256 * 1024**3
MANIFEST_PATH = "_reawote/complete.json"


class GcsBatchError(Exception):
    def __init__(self):
        super().__init__("GCS_BATCH_INVALID")


def _check(condition):
    if not condition:
        raise GcsBatchError()


class MaterialBinding(Strict):
    material_id: Job
    execution_id: Job
    batch_item_sha256: Sha
    csv_row_sha256: Sha
    revision_hash: Sha
    content_context_hash: Sha
    worker_request_sha256: Sha
    packaging_proof_sha256: Sha


class PlannedObject(Strict):
    relative_path: str
    size: Annotated[int, Field(ge=1, le=16 * 1024**3)]
    sha256: Sha
    material_id: Job | None
    source_path: str | None


class PlanBody(Strict):
    version: Literal[1] = 1
    layout: Literal["INTERNAL_STAGING_V1"] = "INTERNAL_STAGING_V1"
    job_id: Job
    batch_id: Job
    batch_snapshot_sha256: Sha
    bucket_name: str
    staging_prefix: str
    materials: Annotated[tuple[MaterialBinding, ...], Field(min_length=1, max_length=100)]
    objects: Annotated[tuple[PlannedObject, ...], Field(min_length=3, max_length=MAX_BATCH_FILES)]

    @model_validator(mode="after")
    def check(self):
        configuration = GcsConfiguration(enabled=True, bucket_name=self.bucket_name,
            staging_prefix=self.staging_prefix)
        identifiers = [item.material_id for item in self.materials]
        _check(identifiers == sorted(set(identifiers)))
        _check(len({item.execution_id for item in self.materials}) == len(self.materials))
        paths = [item.relative_path for item in self.objects]
        _check(paths == sorted(set(paths)) and MANIFEST_PATH not in paths)
        _check(sum(item.size for item in self.objects) <= MAX_BATCH_BYTES)
        csv = [item for item in self.objects if item.material_id is None]
        _check(len(csv) == 1 and csv[0].relative_path == "publication.csv"
               and csv[0].source_path is None and csv[0].size <= 32 * 1024**2)
        for item in self.objects:
            GcsObjectSpec(job_id=self.job_id, binding_sha256="0" * 64,
                relative_path=item.relative_path, size=item.size, sha256=item.sha256).object_name(configuration)
            if item.material_id is not None:
                _check(item.material_id in identifiers and item.source_path is not None
                       and item.relative_path == f"materials/{item.material_id}/{item.source_path}")
        for identifier in identifiers:
            sources = [item.source_path for item in self.objects if item.material_id == identifier]
            _check("metadata.json" in sources and any(path.endswith(".zip") for path in sources))
        return self


class StagingPlan(Strict):
    body: PlanBody
    sha256: Sha

    @model_validator(mode="after")
    def check(self):
        _check(canonical_hash(self.body.model_dump(mode="json")) == self.sha256)
        return self

    def specifications(self) -> tuple[GcsObjectSpec, ...]:
        bound = _plan(self)
        return tuple(GcsObjectSpec(job_id=bound.body.job_id, binding_sha256=bound.sha256,
            relative_path=item.relative_path, size=item.size, sha256=item.sha256) for item in bound.body.objects)


@dataclass(frozen=True)
class PackageInput:
    """Server-loaded bindings; not accepted directly from an HTTP client."""
    material_id: UUID
    batch_item_sha256: str
    prepared: PreparedPackaging
    report: dict
    result: DispatchedPackagingResult


@dataclass(frozen=True)
class CompletionManifest:
    data: bytes
    specification: GcsObjectSpec


def _plan(value):
    try:
        return StagingPlan.model_validate(value.model_dump(mode="python", warnings="error"))
    except (ValueError, TypeError, AttributeError, ArithmeticError, RecursionError):
        raise GcsBatchError() from None


def compile_staging_plan(*, job_id: UUID, batch_id: UUID, batch_snapshot_sha256: str,
                         csv_bytes: bytes, csv_sha256: str, rows: tuple[PublicationCsvRow, ...],
                         packages: tuple[PackageInput, ...], configuration: GcsConfiguration) -> StagingPlan:
    try:
        _check(type(csv_bytes) is bytes and 1 <= len(csv_bytes) <= 32 * 1024**2)
        _check(1 <= len(rows) <= 100 and len(packages) == len(rows))
        validated_rows = tuple(PublicationCsvRow.model_validate(row.model_dump(mode="python", warnings="error")) for row in rows)
        csv = render_publication_csv(validated_rows)
        _check(csv.data == csv_bytes and csv.sha256 == csv_sha256)
        row_by_id = {row.material_id: row for row in validated_rows}
        _check({item.material_id for item in packages} == set(row_by_id)
               and len({item.material_id for item in packages}) == len(packages))
        materials = []
        files = [PlannedObject(relative_path="publication.csv", size=len(csv_bytes), sha256=csv_sha256,
            material_id=None, source_path=None)]
        for package in packages:
            row = row_by_id[package.material_id]
            prepared = PreparedPackaging.model_validate(package.prepared.model_dump(mode="python", warnings="error"))
            result = DispatchedPackagingResult.model_validate(package.result.model_dump(mode="python", warnings="error"))
            result.verify_request(prepared, package.report)
            _check(result.status == "READY" and result.terminal == "OPEN" and result.stored is not None)
            _check(prepared.request.parts[-1] == row.identity_name)
            materials.append(MaterialBinding(material_id=str(row.material_id), execution_id=result.operation_id,
                batch_item_sha256=package.batch_item_sha256, csv_row_sha256=canonical_hash(row.model_dump(mode="json")),
                revision_hash=row.revision_hash, content_context_hash=row.content_context_hash,
                worker_request_sha256=prepared.request_hash, packaging_proof_sha256=result.stored.proof_sha256))
            for item in result.stored.payload.files:
                files.append(PlannedObject(relative_path=f"materials/{row.material_id}/{item.path}", size=item.size,
                    sha256=item.sha256, material_id=str(row.material_id), source_path=item.path))
            _check(len(files) <= MAX_BATCH_FILES)
        body = PlanBody(job_id=str(job_id), batch_id=str(batch_id), batch_snapshot_sha256=batch_snapshot_sha256,
            bucket_name=configuration.bucket_name, staging_prefix=configuration.staging_prefix,
            materials=tuple(sorted(materials, key=lambda item: item.material_id)),
            objects=tuple(sorted(files, key=lambda item: item.relative_path)))
        return StagingPlan(body=body, sha256=canonical_hash(body.model_dump(mode="json")))
    except GcsBatchError:
        raise
    except (ValueError, TypeError, AttributeError, KeyError, ArithmeticError, RecursionError):
        raise GcsBatchError() from None


def completion_manifest(plan: StagingPlan, receipts: tuple[GcsObjectReceipt, ...]) -> CompletionManifest:
    """An incomplete/substituted receipt set cannot produce a completion marker."""
    try:
        bound = _plan(plan)
        expected = {spec.relative_path: spec for spec in bound.specifications()}
        _check(len(receipts) == len(expected))
        configuration = GcsConfiguration(enabled=True, bucket_name=bound.body.bucket_name,
            staging_prefix=bound.body.staging_prefix)
        verified = {}
        for receipt in receipts:
            receipt = GcsObjectReceipt.model_validate(receipt.model_dump(mode="python", warnings="error"))
            path = receipt.spec.relative_path
            _check(path not in verified and path in expected and receipt.spec == expected[path]
                   and receipt.bucket_name == configuration.bucket_name
                   and receipt.object_name == expected[path].object_name(configuration))
            verified[path] = receipt
        document = {"version": 1, "layout": "INTERNAL_STAGING_V1", "job_id": bound.body.job_id,
            "batch_id": bound.body.batch_id, "plan_sha256": bound.sha256,
            "objects": [verified[path].model_dump(mode="json") for path in sorted(verified)]}
        data = (json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
        _check(len(data) <= 32 * 1024**2)
        specification = GcsObjectSpec(job_id=bound.body.job_id, binding_sha256=bound.sha256,
            relative_path=MANIFEST_PATH, size=len(data), sha256=hashlib.sha256(data).hexdigest())
        return CompletionManifest(data, specification)
    except GcsBatchError:
        raise
    except (ValueError, TypeError, AttributeError, KeyError, ArithmeticError, RecursionError):
        raise GcsBatchError() from None
