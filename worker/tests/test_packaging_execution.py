"""Synthetic Linux packaging, crashes and adversarial recovery; no live services."""
from contextlib import contextmanager
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from app import packaging_execution as execution, packaging_store
from app.packaging_execution import (ExecutionLimits, ExecutionRoots, PackagingExecutionError,
    execute_packaging, prepare_packaging_request, reconcile_packaging)
from app.packaging_lease import open_execution_lease
from app.packaging_plan import _digest
from test_packaging_assembly import CURRENT, IDENTITY, LEGACY, make, snapshot, runtime


@pytest.fixture
def job(tmp_path):
    source = make(tmp_path, size=(1024, 1024), master="1K")
    artifacts = tmp_path / "retained"; artifacts.mkdir(mode=0o700)
    journal = tmp_path / "journal"; journal.mkdir(mode=0o700)
    roots = ExecutionRoots(source[0], source[1], artifacts, journal)
    request = prepare_packaging_request(source[3], operation_id=uuid4(), parts=(IDENTITY,),
        expected_source_revision_hash=source[3]["inventory"]["source_revision_hash"],
        expected_technical_report_hash=_digest({key: value for key, value in source[3].items() if key != "inventory"}),
        approval_context_hash="a" * 64, policy=CURRENT, storage_timezone="UTC")
    return source, roots, request


def run(job, **changes):
    source, roots, request = job
    return execute_packaging(changes.pop("request", request), changes.pop("report", source[3]), roots=changes.pop("roots", roots), **changes)


def recover(job, **changes):
    return reconcile_packaging(changes.pop("request", job[2]), roots=changes.pop("roots", job[1]), **changes)


def state(job):
    return json.loads((job[1].journal / str(job[2].operation_id) / "state.json").read_text())


class Interrupted(BaseException): pass


def interrupt_before_staging(job, monkeypatch):
    @contextmanager
    def stop(*args, **kwargs):
        raise Interrupted()
        yield
    with monkeypatch.context() as patch:
        patch.setattr(execution, "stage_packaging_inputs", stop)
        with pytest.raises(Interrupted): run(job)


def test_complete_execution_exact_replay_and_recovery_without_nas(job, monkeypatch):
    before = snapshot(job[0][2])
    result = run(job)
    assert result.status == "READY" and result.attempt == 1 and result.stored.attempt == 1
    assert result.request_hash == job[2].sha256 and result.stored.plan_hash == job[2].plan_hash
    assert state(job)["status"] == "READY" and state(job)["attempts"][0]["cleaned"]
    assert list(job[1].workspace.iterdir()) == [] and snapshot(job[0][2]) == before
    def forbidden(*args, **kwargs): pytest.fail("Recovery must not copy or read NAS")
    monkeypatch.setattr(execution, "stage_packaging_inputs", forbidden)
    assert run(job) == result
    job[1].materials.rename(job[1].materials.with_name("offline-materials"))
    assert recover(job) == result and run(job, retry=True) == result


def test_actual_rectangular_conversion_and_all_resolutions_execute_under_lease(tmp_path, monkeypatch):
    source = make(tmp_path)
    roots = ExecutionRoots(source[0], source[1], tmp_path / "retained", tmp_path / "journal")
    roots.artifacts.mkdir(mode=0o700); roots.journal.mkdir(mode=0o700)
    request = prepare_packaging_request(source[3], operation_id=uuid4(), parts=(IDENTITY,),
        expected_source_revision_hash=source[3]["inventory"]["source_revision_hash"],
        expected_technical_report_hash=_digest({key: value for key, value in source[3].items() if key != "inventory"}),
        approval_context_hash="b" * 64, policy=LEGACY, storage_timezone="Europe/Prague")
    from app.packaging_lease import inherited_lease_fds
    real = subprocess.Popen; calls = []
    def spawn(*args, **kwargs):
        assert inherited_lease_fds()[0] in kwargs["pass_fds"]
        calls.append(1)
        return real(*args, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", spawn)
    result = execute_packaging(request, source[3], roots=roots)
    assert result.status == "READY" and len(calls) >= 8
    assert len(result.stored.payload["bundle"]["archives"]) == 2 and not list(roots.workspace.iterdir())


@pytest.mark.parametrize("change", ["report", "source", "approval", "policy", "timezone", "plan", "budget"])
def test_conflicting_request_or_approved_report_cannot_reuse_operation(job, change):
    first = run(job); before = (job[1].journal / str(job[2].operation_id) / "state.json").read_bytes()
    request = job[2]; report = json.loads(json.dumps(job[0][3]))
    if change == "report": report["warnings"].append({"code": "CHANGED", "message": "Synthetic"})
    elif change == "source": request = replace(request, source_revision_hash="b" * 64)
    elif change == "approval": request = replace(request, approval_context_hash="b" * 64)
    elif change == "policy": request = replace(request, policy=LEGACY)
    elif change == "timezone": request = replace(request, storage_timezone="Europe/Prague")
    elif change == "plan": request = replace(request, plan_hash="b" * 64)
    else: request = replace(request, limits=replace(request.limits, seconds=1700))
    with pytest.raises(PackagingExecutionError): run(job, request=request, report=report, retry=True)
    assert (job[1].journal / str(job[2].operation_id) / "state.json").read_bytes() == before
    assert recover(job) == first


def test_interrupted_work_requires_explicit_retry_and_records_attempt_history(job, monkeypatch):
    interrupt_before_staging(job, monkeypatch)
    assert state(job)["status"] == "WORKING" and len(list(job[1].workspace.iterdir())) == 1
    result = run(job)
    assert result.status == "RETRY_REQUIRED" and result.stored is None and result.attempt == 1
    assert list(job[1].workspace.iterdir()) == []
    assert run(job) == result  # Replaying the original request never begins attempt 2.
    result = run(job, retry=True)
    assert result.status == "READY" and result.attempt == 2
    assert all(item["cleaned"] for item in state(job)["attempts"])
    assert recover(job) == result


def test_changed_source_is_refused_even_on_authorized_retry(job, monkeypatch):
    interrupt_before_staging(job, monkeypatch)
    (job[0][2] / "SOURCE" / "original.sbs").write_bytes(b"New unapproved source")
    with pytest.raises(PackagingExecutionError, match="SOURCE_CHANGED"): run(job, retry=True)
    assert state(job)["status"] == "WORKING" and len(state(job)["attempts"]) == 2
    assert list(job[1].workspace.iterdir()) == []
    assert not (job[1].artifacts / str(job[2].operation_id)).exists()
    assert recover(job).status == "RETRY_REQUIRED" and list(job[1].workspace.iterdir()) == []


@pytest.mark.parametrize("pair", [("workspace", "materials"), ("artifacts", "materials"), ("journal", "materials"),
    ("workspace", "artifacts"), ("journal", "workspace"), ("journal", "artifacts")])
def test_overlapping_roots_are_refused_before_reservation(job, pair):
    roots = replace(job[1], **{pair[0]: getattr(job[1], pair[1])})
    with pytest.raises(PackagingExecutionError, match="ROOT_OVERLAP"): run(job, roots=roots)
    assert not list(job[1].journal.iterdir())


@pytest.mark.parametrize("key", ["workspace", "artifacts", "journal"])
def test_symlinked_or_public_roots_are_refused(job, key):
    root = getattr(job[1], key); root.chmod(0o755)
    with pytest.raises(PackagingExecutionError): run(job)
    root.chmod(0o700); moved = root.with_name(key + "-preserved"); root.rename(moved); root.symlink_to(moved)
    with pytest.raises(PackagingExecutionError): run(job)
    assert list(moved.iterdir()) == []


def test_unknown_journal_operation_is_preserved(job):
    operation = job[1].journal / str(job[2].operation_id)
    operation.mkdir(mode=0o700)
    with pytest.raises(PackagingExecutionError, match="UNKNOWN_OPERATION"): run(job)
    assert list(operation.iterdir()) == []


@pytest.mark.parametrize("change", ["extra", "replaced-root", "replaced-journal", "replaced-lock", "corrupt-state"])
def test_uncertain_ownership_is_preserved_for_operator_review(job, monkeypatch, change):
    interrupt_before_staging(job, monkeypatch)
    record = state(job); workspace = job[1].workspace / record["attempts"][-1]["workspace"]
    operation = job[1].journal / str(job[2].operation_id)
    if change == "extra": (workspace / "unknown").write_text("Keep")
    elif change == "replaced-root":
        workspace.rename(workspace.with_name("preserved-workspace")); workspace.mkdir(mode=0o700); (workspace / "keep").write_text("Keep")
    elif change == "replaced-journal":
        operation.rename(operation.with_name("preserved-operation")); operation.mkdir(mode=0o700)
        (operation / "state.json").write_text(json.dumps(record)); (operation / "state.json").chmod(0o600)
    elif change == "replaced-lock":
        lock = operation / "execution.lock"; lock.rename(operation / "preserved.lock")
        lock.write_bytes(b""); lock.chmod(0o600)
    else:
        record["attempts"][0]["identity"] = ["invalid", 0]
        (operation / "state.json").write_text(json.dumps(record))
    with pytest.raises(PackagingExecutionError): recover(job)
    assert workspace.exists()
    if change == "extra": assert (workspace / "unknown").read_text() == "Keep"
    if change == "replaced-root": assert (workspace / "keep").read_text() == "Keep"


def test_live_execution_lease_fences_cleanup_across_processes(job, monkeypatch):
    interrupt_before_staging(job, monkeypatch)
    record = state(job); operation = job[1].journal / str(job[2].operation_id)
    directory = os.open(operation, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with open_execution_lease(directory, expected_identity=tuple(record["lease_identity"])) as lease:
            # Separate Python process avoids relying on ContextVar's nested-call guard.
            config = job[1].journal.parent / "input.json"; _config(job, config)
            code = CHILD_PREFIX + "\ntry:\n execution.reconcile_packaging(request, roots=roots)\nexcept execution.PackagingExecutionError as error:\n print(str(error), flush=True)\nelse:\n raise SystemExit(2)\n"
            result = subprocess.run([sys.executable, "-c", code, str(config)], cwd="/app",
                env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True, timeout=10)
            assert result.returncode == 0 and result.stdout.strip() == "PACKAGING_LEASE_BUSY"
            assert len(list(job[1].workspace.iterdir())) == 1
    finally: os.close(directory)
    assert recover(job).status == "RETRY_REQUIRED"


def test_ready_workspace_name_reappearance_is_preserved(job):
    run(job); record = state(job)
    path = job[1].workspace / record["attempts"][0]["workspace"]
    path.mkdir(mode=0o700); (path / "keep").write_text("Keep")
    with pytest.raises(PackagingExecutionError, match="WORKSPACE_CHANGED"): recover(job)
    assert (path / "keep").read_text() == "Keep"


def test_retained_proof_corruption_prevents_success_and_retry(job):
    result = run(job)
    manifest = job[1].artifacts / str(job[2].operation_id) / "ready" / "metadata.json"
    manifest.chmod(0o600); manifest.write_bytes(b"x" * manifest.stat().st_size); manifest.chmod(0o400)
    for call in (lambda: recover(job), lambda: run(job, retry=True)):
        with pytest.raises(PackagingExecutionError, match="ARTIFACT_CHANGED"): call()
    assert state(job)["result"]["proof_sha256"] == result.stored.proof_sha256


def _config(job, path):
    path.write_text(json.dumps({"report": job[0][3], "request": job[2].document(),
        "roots": {key: str(value) for key, value in asdict(job[1]).items()}}))
    path.chmod(0o600)


CHILD_PREFIX = """
import json, os, sys
from pathlib import Path
from uuid import UUID
from app import packaging_execution as execution, packaging_store
value = json.loads(Path(sys.argv[1]).read_text())
data = value['request']; report = value['report']
request = execution.PackagingRequest(UUID(data['operation_id']), tuple(data['parts']), data['source_revision_hash'],
    data['technical_report_hash'], data['approval_context_hash'], data['policy'], data['storage_timezone'],
    data['plan_hash'], execution.ExecutionLimits(**data['limits']))
roots = execution.ExecutionRoots(**{key: Path(path) for key, path in value['roots'].items()})
"""


@pytest.mark.parametrize("point", ["first-record-gap", "workspace-record-gap", "before-stage", "partial-stage",
    "partial-retention", "after-retained-rename", "before-cleanup", "after-cleanup", "after-ready"])
def test_real_process_death_recovers_only_known_work_and_never_restarts_implicitly(job, tmp_path, point):
    config = tmp_path / "input.json"; _config(job, config)
    script = CHILD_PREFIX + """
point = sys.argv[2]
write = execution.FileJournal.write
stage = execution.stage_packaging_inputs
cleanup = execution._cleanup
rename = packaging_store.rename_noreplace
copy = packaging_store._copy_file
def writing(self, value):
    if value.get('kind') == 'PBR_PACKAGING_EXECUTION':
        if point == 'first-record-gap' and value['status'] == 'RESERVED': os._exit(75)
        if point == 'workspace-record-gap' and value['status'] == 'WORKING': os._exit(75)
    write(self, value)
    if point == 'after-ready' and value.get('kind') == 'PBR_PACKAGING_EXECUTION' and value['status'] == 'READY': os._exit(75)
def staging(*args, **kwargs):
    if point == 'before-stage': os._exit(75)
    return stage(*args, **kwargs)
def clearing(*args, **kwargs):
    if point == 'before-cleanup': os._exit(75)
    cleanup(*args, **kwargs)
    if point == 'after-cleanup': os._exit(75)
def renaming(*args):
    rename(*args)
    if point == 'after-retained-rename': os._exit(75)
def copying(*args):
    copy(*args)
    if point == 'partial-retention': os._exit(75)
execution.FileJournal.write = writing
execution.stage_packaging_inputs = staging
execution._cleanup = clearing
packaging_store.rename_noreplace = renaming
packaging_store._copy_file = copying
if point == 'partial-stage':
    from app import packaging_stage
    real_copy = packaging_stage._copy
    def copy_input(*args):
        real_copy(*args)
        os._exit(75)
    packaging_stage._copy = copy_input
execution.execute_packaging(request, report, roots=roots)
raise SystemExit(2)
"""
    before = snapshot(job[0][2])
    process = subprocess.run([sys.executable, "-c", script, str(config), point], cwd="/app",
        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
    assert process.returncode == 75 and snapshot(job[0][2]) == before
    if point in {"first-record-gap", "workspace-record-gap"}:
        with pytest.raises(PackagingExecutionError, match="UNKNOWN_OPERATION|RECOVERY_REQUIRED"): recover(job)
        with pytest.raises(PackagingExecutionError): run(job, retry=True)
        if point == "workspace-record-gap": assert len(list(job[1].workspace.iterdir())) == 1
        return
    # A real killed process leaves stage/assembly directories. Recovery removes
    # only those inside the independently recorded outer workspace.
    result = recover(job)
    assert not list(job[1].workspace.iterdir())
    if point in {"before-stage", "partial-stage", "partial-retention"}:
        assert result.status == "RETRY_REQUIRED" and run(job) == result
        result = run(job, retry=True)
        assert result.attempt == 2 and result.status == "READY"
        if point == "partial-retention": assert result.stored.attempt == 2
    else:
        assert result.status == "READY" and result.attempt == 1 and result.stored.attempt == 1
    assert recover(job) == result and snapshot(job[0][2]) == before


@pytest.mark.parametrize("field", ["staged_bytes", "generated_bytes", "retained_bytes"])
def test_byte_limits_fail_without_false_ready_and_allow_only_guarded_recovery(job, field):
    before = snapshot(job[0][2])
    request = replace(job[2], limits=replace(job[2].limits, **{field: 1}))
    limited = job[0], job[1], request
    with pytest.raises(PackagingExecutionError, match="SIZE_LIMIT"): run(limited)
    assert state(limited)["status"] == "WORKING"
    assert not list(job[1].workspace.iterdir()) and snapshot(job[0][2]) == before
    result = recover(limited)
    assert result.status == "RETRY_REQUIRED" and result.stored is None and not list(job[1].workspace.iterdir())


@pytest.mark.parametrize("failure", [
    execution.PackagingStageError("PACKAGING_SOURCE_CHANGED"),
    execution.PackagingConversionError("PACKAGING_CONVERSION_TIME_LIMIT"),
    PackagingExecutionError("PACKAGING_EXECUTION_TIME_LIMIT"),
    OSError("synthetic private diagnostic"),
])
def test_handled_failure_cleans_owned_workspace_without_claiming_a_result(job, monkeypatch, failure):
    before = snapshot(job[0][2]); recorded = []
    @contextmanager
    def failed(*args, **kwargs):
        recorded.append(state(job))
        assert recorded[0]["status"] == "WORKING"
        raise failure
        yield
    with monkeypatch.context() as patch:
        patch.setattr(execution, "stage_packaging_inputs", failed)
        with pytest.raises(PackagingExecutionError, match="^PACKAGING_[A-Z_]+$"):
            run(job)
    assert not list(job[1].workspace.iterdir()) and not list(job[1].artifacts.iterdir())
    assert state(job) == recorded[0] and not state(job)["attempts"][0]["cleaned"]
    assert snapshot(job[0][2]) == before
    # Only explicit reconciliation establishes incompleteness; replay cannot
    # convert again. A new authorized attempt is still needed.
    assert run(job).status == "RETRY_REQUIRED"
    assert len(state(job)["attempts"]) == 1
    assert run(job, retry=True).status == "READY" and len(state(job)["attempts"]) == 2


@pytest.mark.parametrize("point", ["retained-return", "execution-journal"])
def test_failure_after_retention_cleans_temporary_work_but_preserves_committed_output(job, monkeypatch, point):
    before = snapshot(job[0][2]); saved = []
    retain = execution.retain_packages; write = execution.FileJournal.write
    def retaining(*args, **kwargs):
        result = retain(*args, **kwargs); saved.append(result)
        if point == "retained-return": raise OSError("synthetic private diagnostic")
        return result
    def writing(self, value):
        write(self, value)
        if value.get("kind") == "PBR_PACKAGING_EXECUTION" and value["status"] == "RETAINED":
            raise OSError("synthetic private diagnostic")
    with monkeypatch.context() as patch:
        patch.setattr(execution, "retain_packages", retaining)
        if point == "execution-journal": patch.setattr(execution.FileJournal, "write", writing)
        with pytest.raises(PackagingExecutionError, match="^PACKAGING_ASSEMBLY_FAILED$"): run(job)
    assert not list(job[1].workspace.iterdir()) and snapshot(job[0][2]) == before
    assert state(job)["status"] == ("WORKING" if point == "retained-return" else "RETAINED")
    retained_before = snapshot(job[1].artifacts)
    job[1].materials.rename(job[1].materials.with_name("offline-materials"))
    result = recover(job)
    assert result.status == "READY" and result.attempt == 1 and result.stored == saved[0]
    assert snapshot(job[1].artifacts) == retained_before and run(job, retry=True) == result


@pytest.mark.parametrize("change", ["unknown-child", "replaced-root"])
def test_failure_cleanup_preserves_ambiguous_work_and_protected_source(job, monkeypatch, change):
    before = snapshot(job[0][2]); workspaces = []
    @contextmanager
    def failed(*args, **kwargs):
        workspace = job[1].workspace / state(job)["attempts"][-1]["workspace"]
        workspaces.append(workspace)
        if change == "replaced-root":
            workspace.rename(workspace.with_name("preserved-workspace"))
            workspace.symlink_to(job[0][2], target_is_directory=True)
        else: (workspace / "unknown").write_text("Keep")
        raise execution.PackagingStageError("PACKAGING_SOURCE_CHANGED")
        yield
    with monkeypatch.context() as patch:
        patch.setattr(execution, "stage_packaging_inputs", failed)
        with pytest.raises(PackagingExecutionError, match="UNKNOWN_WORKSPACE_FILE|RECOVERY_REQUIRED"): run(job)
    assert workspaces[0].exists() and snapshot(job[0][2]) == before
    assert state(job)["status"] == "WORKING" and not state(job)["attempts"][0]["cleaned"]
    if change == "unknown-child": assert (workspaces[0] / "unknown").read_text() == "Keep"
    else: assert workspaces[0].is_symlink()
    with pytest.raises(PackagingExecutionError, match="UNKNOWN_WORKSPACE_FILE|RECOVERY_REQUIRED"): recover(job)


def test_ready_missing_artifact_operation_never_becomes_retryable(job):
    run(job)
    operation = job[1].artifacts / str(job[2].operation_id)
    operation.rename(operation.with_name("preserved-result"))
    with pytest.raises(PackagingExecutionError, match="RESULT_MISSING"): recover(job)
    with pytest.raises(PackagingExecutionError, match="RESULT_MISSING"): run(job, retry=True)
    assert state(job)["status"] == "READY"


def test_independently_saved_result_proof_must_match_retention(job):
    run(job)
    record = state(job); record["result"]["proof_sha256"] = "0" * 64
    path = job[1].journal / str(job[2].operation_id) / "state.json"
    path.write_text(json.dumps(record)); before = path.read_bytes()
    with pytest.raises(PackagingExecutionError, match="RESULT_CHANGED"): recover(job)
    assert path.read_bytes() == before


def test_retry_attempts_are_bounded(job, monkeypatch):
    monkeypatch.setattr(execution, "MAX_ATTEMPTS", 2)
    interrupt_before_staging(job, monkeypatch)
    @contextmanager
    def stop(*args, **kwargs):
        raise Interrupted()
        yield
    with monkeypatch.context() as patch:
        patch.setattr(execution, "stage_packaging_inputs", stop)
        with pytest.raises(Interrupted): run(job, retry=True)
    assert recover(job).status == "RETRY_REQUIRED" and len(state(job)["attempts"]) == 2
    with pytest.raises(PackagingExecutionError, match="ATTEMPT_LIMIT"): run(job, retry=True)
    assert len(state(job)["attempts"]) == 2


def test_known_orphan_symlink_is_unlinked_without_following_protected_target(job, monkeypatch, tmp_path):
    interrupt_before_staging(job, monkeypatch)
    record = state(job); workspace = job[1].workspace / record["attempts"][0]["workspace"]
    protected = tmp_path / "protected"; protected.mkdir(); (protected / "keep").write_text("Keep")
    (workspace / ("packaging-" + str(job[2].operation_id))).symlink_to(protected)
    assert recover(job).status == "RETRY_REQUIRED"
    assert (protected / "keep").read_text() == "Keep" and not workspace.exists()


def test_replaced_outer_workspace_symlink_is_preserved(job, monkeypatch, tmp_path):
    interrupt_before_staging(job, monkeypatch)
    record = state(job); workspace = job[1].workspace / record["attempts"][0]["workspace"]
    workspace.rename(workspace.with_name("preserved-workspace"))
    protected = tmp_path / "protected"; protected.mkdir(); (protected / "keep").write_text("Keep")
    workspace.symlink_to(protected)
    with pytest.raises(PackagingExecutionError, match="RECOVERY_REQUIRED"): recover(job)
    assert (protected / "keep").read_text() == "Keep" and workspace.is_symlink()


def test_existing_artifact_operation_is_not_adopted_on_first_execution(job):
    operation = job[1].artifacts / str(job[2].operation_id)
    operation.mkdir(mode=0o700); (operation / "keep").write_text("Keep")
    with pytest.raises(PackagingExecutionError, match="UNEXPECTED_RESULT"): run(job)
    assert (operation / "keep").read_text() == "Keep" and not list(job[1].workspace.iterdir())


def test_unknown_workspace_child_prevents_deletion_even_after_retention(job, monkeypatch):
    original = execution._cleanup
    def injected(request, handles, record, verify):
        workspace = job[1].workspace / record["attempts"][-1]["workspace"]
        (workspace / "unknown").write_text("Keep")
        original(request, handles, record, verify)
    monkeypatch.setattr(execution, "_cleanup", injected)
    with pytest.raises(PackagingExecutionError, match="UNKNOWN_WORKSPACE_FILE"): run(job)
    assert state(job)["status"] == "RETAINED"
    workspace = job[1].workspace / state(job)["attempts"][-1]["workspace"]
    assert (workspace / "unknown").read_text() == "Keep"
    monkeypatch.setattr(execution, "_cleanup", original)
    with pytest.raises(PackagingExecutionError, match="UNKNOWN_WORKSPACE_FILE"): recover(job)


@pytest.mark.parametrize("phase", ["before-first-state", "after-workspace"])
def test_root_replacement_during_execution_cannot_report_success(job, monkeypatch, phase):
    real = execution.FileJournal.write; replaced = False
    def writing(self, value):
        nonlocal replaced
        real(self, value)
        if not replaced and value.get("kind") == "PBR_PACKAGING_EXECUTION" and value["status"] == ("RESERVED" if phase == "before-first-state" else "WORKING"):
            replaced = True
            root = job[1].workspace
            root.rename(root.with_name("preserved-workspace-root"))
            root.mkdir(mode=0o700); (root / "keep").write_text("Keep")
    monkeypatch.setattr(execution.FileJournal, "write", writing)
    with pytest.raises(PackagingExecutionError, match="ROOT_CHANGED|ROOT_MISMATCH|FAILED"): run(job)
    assert (job[1].workspace / "keep").read_text() == "Keep"
    assert state(job)["status"] != "READY"
