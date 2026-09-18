"""Batch completeness and immutable bindings using real synthetic worker proofs."""
from dataclasses import replace
import hashlib
import json
from uuid import UUID, uuid4

import pytest

from app.gcs_batch import (GcsBatchError, PackageInput, compile_staging_plan, completion_manifest)
from app.gcs_contract import GcsConfiguration, GcsObjectReceipt
from app.material_review import canonical_hash
from app.packaging_contract import DispatchedPackagingResult
from app.publication_csv import render_publication_csv
from test_packaging_client import fixture, prepared
from test_publication_csv import row


def inputs():
    value = fixture("packaging-dispatch-contract.json")
    material = row(identity_name=value["prepared"]["request"]["parts"][-1])
    csv = render_publication_csv((material,))
    return {"job_id": uuid4(), "batch_id": uuid4(), "batch_snapshot_sha256": "c" * 64,
        "csv_bytes": csv.data, "csv_sha256": csv.sha256, "rows": (material,),
        "packages": (PackageInput(material.material_id, "d" * 64, prepared(value), value["report"],
            DispatchedPackagingResult.model_validate_json(json.dumps(value["result"]))),),
        "configuration": GcsConfiguration(enabled=True, bucket_name="synthetic-reawote-staging",
            staging_prefix="isolated/contracts")}


def receipts(plan):
    configuration = GcsConfiguration(enabled=True, bucket_name=plan.body.bucket_name,
        staging_prefix=plan.body.staging_prefix)
    return tuple(GcsObjectReceipt(spec=spec, bucket_name=configuration.bucket_name,
        object_name=spec.object_name(configuration), generation=str(index + 100), metageneration="1")
        for index, spec in enumerate(plan.specifications()))


def test_complete_exact_csv_all_retained_files_and_deterministic_completion_marker():
    values = inputs()
    plan = compile_staging_plan(**values)
    assert plan == compile_staging_plan(**values)
    package = values["packages"][0]
    expected = {f"materials/{package.material_id}/{file.path}": (file.size, file.sha256)
        for file in package.result.stored.payload.files}
    expected["publication.csv"] = (len(values["csv_bytes"]), values["csv_sha256"])
    assert {obj.relative_path: (obj.size, obj.sha256) for obj in plan.body.objects} == expected
    assert plan.body.layout == "INTERNAL_STAGING_V1"
    assert plan.body.materials[0].packaging_proof_sha256 == package.result.stored.proof_sha256
    assert all(spec.binding_sha256 == plan.sha256 for spec in plan.specifications())
    observations = receipts(plan)
    manifest = completion_manifest(plan, observations)
    assert manifest == completion_manifest(plan, tuple(reversed(observations)))
    assert hashlib.sha256(manifest.data).hexdigest() == manifest.specification.sha256
    assert manifest.specification.relative_path == "_reawote/complete.json"
    parsed = json.loads(manifest.data)
    assert parsed["plan_sha256"] == plan.sha256
    assert len(parsed["objects"]) == len(plan.body.objects)
    assert manifest.specification.relative_path not in expected


@pytest.mark.parametrize("change", ["csv-bytes", "csv-hash", "csv-rows", "missing-package", "duplicate-package",
    "wrong-material", "wrong-identity", "request-hash", "proof-hash", "rehashed-extra-file", "closed",
    "wrong-report", "bad-snapshot-hash", "no-target", "non-v4-job"])
def test_mismatched_incomplete_or_unverified_inputs_never_compile(change):
    values = inputs()
    package = values["packages"][0]
    if change == "csv-bytes": values["csv_bytes"] += b"x"
    elif change == "csv-hash": values["csv_sha256"] = "f" * 64
    elif change == "csv-rows": values["rows"] = (values["rows"][0].model_copy(update={"name": "Changed"}),)
    elif change == "missing-package": values["packages"] = ()
    elif change == "duplicate-package": values["packages"] *= 2
    elif change == "wrong-material": values["packages"] = (replace(package, material_id=uuid4()),)
    elif change == "wrong-identity":
        values["rows"] = (values["rows"][0].model_copy(update={"identity_name": "Different"}),)
        csv = render_publication_csv(values["rows"])
        values.update(csv_bytes=csv.data, csv_sha256=csv.sha256)
    elif change == "request-hash":
        values["packages"] = (replace(package, prepared=package.prepared.model_copy(update={"request_hash": "f" * 64})),)
    elif change in {"proof-hash", "rehashed-extra-file"}:
        raw = package.result.model_dump(mode="json")
        if change == "rehashed-extra-file":
            raw["stored"]["payload"]["files"].append({"path": "extra.zip", "size": 1, "sha256": "a" * 64})
            raw["stored"]["payload"]["files"].sort(key=lambda item: item["path"])
            raw["stored"]["proof_sha256"] = canonical_hash(raw["stored"]["payload"])
        else: raw["stored"]["proof_sha256"] = "f" * 64
        # The rehashed extra file passes structural validation but fails the
        # independent report/layout check. A bad outer hash is a forged model.
        result = DispatchedPackagingResult.model_validate_json(json.dumps(raw)) if change == "rehashed-extra-file" else package.result.model_copy(update={"stored": raw["stored"]})
        values["packages"] = (replace(package, result=result),)
    elif change == "closed":
        values["packages"] = (replace(package, result=package.result.model_copy(update={"terminal": "CLOSED"})),)
    elif change == "wrong-report": values["packages"] = (replace(package, report={}),)
    elif change == "bad-snapshot-hash": values["batch_snapshot_sha256"] = "bad"
    elif change == "no-target": values["configuration"] = GcsConfiguration()
    elif change == "non-v4-job": values["job_id"] = UUID(int=1)
    with pytest.raises(GcsBatchError, match="^GCS_BATCH_INVALID$"):
        compile_staging_plan(**values)


@pytest.mark.parametrize("change", ["job", "batch", "snapshot", "item-snapshot", "content", "target", "prefix"])
def test_same_file_bytes_under_changed_provenance_produce_a_different_plan(change):
    values = inputs()
    first = compile_staging_plan(**values)
    if change == "job": values["job_id"] = uuid4()
    elif change == "batch": values["batch_id"] = uuid4()
    elif change == "snapshot": values["batch_snapshot_sha256"] = "f" * 64
    elif change == "item-snapshot": values["packages"] = (replace(values["packages"][0], batch_item_sha256="f" * 64),)
    elif change == "content": values["rows"] = (values["rows"][0].model_copy(update={"content_context_hash": "f" * 64}),)
    elif change == "target": values["configuration"] = values["configuration"].model_copy(update={"bucket_name": "different-target"})
    elif change == "prefix": values["configuration"] = values["configuration"].model_copy(update={"staging_prefix": "different-prefix"})
    second = compile_staging_plan(**values)
    assert first.sha256 != second.sha256
    assert first.body.objects == second.body.objects


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate", "wrong-job", "wrong-plan", "wrong-size",
    "wrong-hash", "wrong-path", "wrong-bucket", "wrong-object", "bad-generation", "bool-size"])
def test_completion_requires_exactly_every_verified_planned_object(change):
    plan = compile_staging_plan(**inputs())
    observations = receipts(plan)
    first = observations[0]
    if change == "missing": observations = observations[:-1]
    elif change == "extra": observations += (first,)
    elif change == "duplicate": observations = (first, first, *observations[2:])
    else:
        modifications = {
            "wrong-job": {"job_id": str(uuid4())}, "wrong-plan": {"binding_sha256": "f" * 64},
            "wrong-size": {"size": first.spec.size + 1}, "wrong-hash": {"sha256": "f" * 64},
            "wrong-path": {"relative_path": "extra.zip"}, "bool-size": {"size": True}}
        if change in modifications:
            first = first.model_copy(update={"spec": first.spec.model_copy(update=modifications[change])})
        else:
            first = first.model_copy(update={
                {"wrong-bucket": "bucket_name", "wrong-object": "object_name", "bad-generation": "generation"}[change]: "bad"})
        observations = (first, *observations[1:])
    with pytest.raises(GcsBatchError):
        completion_manifest(plan, observations)


@pytest.mark.parametrize("change", ["hash", "dropped-csv", "duplicate-file", "unbound-material", "traversal", "marker-collision"])
def test_mutated_plan_is_revalidated_before_object_specs_or_completion(change):
    plan = compile_staging_plan(**inputs())
    observed = receipts(plan)
    body = plan.body.model_dump(mode="python")
    if change == "hash": broken = plan.model_copy(update={"sha256": "f" * 64})
    else:
        files = list(body["objects"])
        if change == "dropped-csv": files = [file for file in files if file["material_id"] is not None]
        elif change == "duplicate-file": files[1] = files[0]
        elif change == "unbound-material": files[0]["material_id"] = str(uuid4())
        elif change == "traversal": files[0]["relative_path"] = "../bad"
        elif change == "marker-collision": files[0]["relative_path"] = "_reawote/complete.json"
        body["objects"] = tuple(files)
        broken = plan.model_copy(update={"body": plan.body.model_copy(update={"objects": tuple(files)})})
    with pytest.raises(GcsBatchError): broken.specifications()
    with pytest.raises(GcsBatchError): completion_manifest(broken, observed)
