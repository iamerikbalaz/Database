"""Real retained artifacts and ordered recovery under the Linux execution lease."""
from contextlib import contextmanager
from dataclasses import replace
import os
import subprocess
import sys
from uuid import UUID, uuid4

import pytest

from app import packaging_execution as execution
from app.packaging_dispatch import PackagingDispatch, dispatch_packaging
from app.packaging_execution import PackagingExecutionError
from test_packaging_assembly import runtime, snapshot
from test_packaging_execution import job, state, run, recover, interrupt_before_staging, Interrupted, _config, CHILD_PREFIX


def command(action="EXECUTE", ordinal=1): return PackagingDispatch(uuid4(), ordinal, action)


def dispatch(job, value):
    return dispatch_packaging(job[2], value, roots=job[1], report=job[0][3] if value.action in {"EXECUTE", "RETRY"} else None)


def test_completed_execution_has_ordered_replay_recovery_and_permanent_closure(job, monkeypatch):
    before = snapshot(job[0][2]); start = command()
    first = dispatch(job, start)
    assert first.result.status == "READY" and first.terminal == "OPEN" and state(job)["version"] == 2
    def forbidden(*args, **kwargs): pytest.fail("Replay must not stage source inputs")
    monkeypatch.setattr(execution, "stage_packaging_inputs", forbidden)
    assert dispatch(job, start) == first
    newer = dispatch(job, command("RECONCILE", 2))
    assert newer.result == first.result
    with pytest.raises(PackagingExecutionError, match="DISPATCH_STALE"): dispatch(job, start)
    closing = command("CLOSE", 3)
    closed = dispatch(job, closing)
    assert closed.terminal == "CLOSED" and closed.result == first.result and dispatch(job, closing) == closed
    with pytest.raises(PackagingExecutionError, match="EXECUTION_CLOSED"): dispatch(job, command("RETRY", 4))
    assert dispatch(job, command("RECONCILE", 5)).result == first.result
    assert snapshot(job[0][2]) == before and list(job[1].workspace.iterdir()) == []


def test_newer_reconcile_fences_a_delayed_initial_command_without_reading_nas(job):
    job[1].materials.rename(job[1].materials.with_name("offline"))
    recovered = dispatch(job, command("RECONCILE", 2))
    assert recovered.result.status == "RETRY_REQUIRED" and recovered.result.attempt == 1
    assert recovered.terminal == "OPEN" and list(job[1].workspace.iterdir()) == []
    with pytest.raises(PackagingExecutionError, match="DISPATCH_STALE"): dispatch(job, command())
    closed = dispatch(job, command("CLOSE", 3))
    assert closed.terminal == "CLOSED" and closed.result.stored is None
    with pytest.raises(PackagingExecutionError, match="EXECUTION_CLOSED"): dispatch(job, command("RETRY", 4))
    assert list(job[1].artifacts.iterdir()) == []


def test_fenced_never_started_work_requires_a_new_explicit_retry(job):
    initial = dispatch(job, command("RECONCILE", 2))
    retry = command("RETRY", 3)
    completed = dispatch(job, retry)
    assert initial.result.attempt == 1 and completed.result.attempt == 2 and completed.result.status == "READY"
    assert dispatch(job, retry) == completed


def test_delayed_retry_after_recovery_or_closure_cannot_start_another_attempt(job, monkeypatch):
    interrupt_before_staging(job, monkeypatch)
    dispatch(job, command("RECONCILE", 2))
    delayed = command("RETRY", 3)
    dispatch(job, command("RECONCILE", 4))
    with pytest.raises(PackagingExecutionError, match="DISPATCH_STALE"): dispatch(job, delayed)
    closing = dispatch(job, command("CLOSE", 5))
    assert closing.terminal == "CLOSED" and closing.result.attempt == 1
    with pytest.raises(PackagingExecutionError, match="DISPATCH_STALE"): dispatch(job, delayed)
    assert len(state(job)["attempts"]) == 1 and list(job[1].artifacts.iterdir()) == []


def test_exact_failed_retry_only_recovers_and_does_not_retry_twice(job, monkeypatch):
    dispatch(job, command("RECONCILE", 2)); retry = command("RETRY", 3)
    @contextmanager
    def stop(*args, **kwargs):
        raise Interrupted()
        yield
    with monkeypatch.context() as patch:
        patch.setattr(execution, "stage_packaging_inputs", stop)
        with pytest.raises(Interrupted): dispatch(job, retry)
    assert state(job)["status"] == "WORKING" and len(state(job)["attempts"]) == 2
    recovered = dispatch(job, retry)
    assert recovered.result.status == "RETRY_REQUIRED" and recovered.result.attempt == 2
    assert dispatch(job, retry) == recovered
    assert dispatch(job, command("RETRY", 4)).result.attempt == 3


def test_handled_dispatch_failure_cleans_work_but_keeps_exact_command_fence(job, monkeypatch):
    initial = command(); before = snapshot(job[0][2])
    @contextmanager
    def failed(*args, **kwargs):
        raise execution.PackagingStageError("PACKAGING_SOURCE_CHANGED")
        yield
    with monkeypatch.context() as patch:
        patch.setattr(execution, "stage_packaging_inputs", failed)
        with pytest.raises(PackagingExecutionError, match="SOURCE_CHANGED"): dispatch(job, initial)
    assert not list(job[1].workspace.iterdir()) and snapshot(job[0][2]) == before
    assert state(job)["dispatch"] == {"command": initial.document(), "terminal": "OPEN"}
    recovered = dispatch(job, initial)
    assert recovered.result.status == "RETRY_REQUIRED" and recovered.result.attempt == 1
    assert dispatch(job, initial) == recovered
    completed = dispatch(job, command("RETRY", 2))
    assert completed.result.status == "READY" and completed.result.attempt == 2


@pytest.mark.parametrize("change", ["id", "action"])
def test_same_ordinal_with_changed_command_is_rejected(job, change):
    original = command("RECONCILE", 2); dispatch(job, original)
    changed = replace(original, **({"id": uuid4()} if change == "id" else {"action": "CLOSE"}))
    before = state(job)
    with pytest.raises(PackagingExecutionError, match="DISPATCH_CONFLICT"): dispatch(job, changed)
    assert state(job) == before


@pytest.mark.parametrize("operation", ["execute", "retry", "reconcile"])
def test_legacy_entry_points_cannot_bypass_an_ordered_execution(job, operation):
    dispatch(job, command("RECONCILE", 2))
    with pytest.raises(PackagingExecutionError, match="DISPATCH_REQUIRED"):
        if operation == "reconcile": recover(job)
        else: run(job, retry=operation == "retry")
    assert len(state(job)["attempts"]) == 1


@pytest.mark.parametrize("value", [PackagingDispatch(UUID(int=0), 1, "EXECUTE"), PackagingDispatch(uuid4(), True, "EXECUTE"),
    PackagingDispatch(uuid4(), 0, "CLOSE"), PackagingDispatch(uuid4(), 2**31, "RECONCILE"),
    PackagingDispatch(uuid4(), 2, "EXECUTE"), PackagingDispatch(uuid4(), 1, "RETRY"),
    PackagingDispatch(uuid4(), 1, "RECONCILE"), PackagingDispatch(uuid4(), 2, "INVALID")])
def test_invalid_command_never_reserves_a_journal(job, value):
    with pytest.raises(PackagingExecutionError, match="DISPATCH_INVALID"): dispatch(job, value)
    assert list(job[1].journal.iterdir()) == []


def test_closing_intent_survives_cleanup_failure_and_refuses_any_later_retry(job, monkeypatch):
    interrupt_before_staging(job, monkeypatch)
    close = command("CLOSE", 2)
    def failed(*args, **kwargs): raise PackagingExecutionError("PACKAGING_EXECUTION_RECOVERY_REQUIRED")
    with monkeypatch.context() as patch:
        patch.setattr(execution, "_cleanup", failed)
        with pytest.raises(PackagingExecutionError, match="RECOVERY_REQUIRED"): dispatch(job, close)
    assert state(job)["dispatch"]["terminal"] == "CLOSING"
    with pytest.raises(PackagingExecutionError, match="EXECUTION_CLOSED"): dispatch(job, command("RETRY", 3))
    recovered = dispatch(job, command("RECONCILE", 4))
    assert recovered.terminal == "CLOSING" and recovered.result.status == "RETRY_REQUIRED"
    assert dispatch(job, command("CLOSE", 5)).terminal == "CLOSED"


@pytest.mark.parametrize("point", ["after-dispatch", "closing", "closed"])
def test_actual_process_death_preserves_dispatch_fence_and_closure_intent(job, tmp_path, point):
    config = tmp_path / "input.json"; _config(job, config)
    identifier = uuid4()
    script = CHILD_PREFIX + """
from app.packaging_dispatch import PackagingDispatch, dispatch_packaging
point=sys.argv[2]
command=PackagingDispatch(UUID(sys.argv[3]), 1, 'EXECUTE' if point=='after-dispatch' else 'CLOSE')
write=execution.FileJournal.write
def writing(self,value):
    write(self,value)
    if value.get('version')==2:
        terminal=value['dispatch']['terminal']
        if point=='after-dispatch' or point=='closing' and terminal=='CLOSING' or point=='closed' and terminal=='CLOSED': os._exit(86)
execution.FileJournal.write=writing
dispatch_packaging(request, command, roots=roots, report=report if command.action=='EXECUTE' else None)
raise SystemExit(2)
"""
    process = subprocess.run([sys.executable, "-c", script, str(config), point, str(identifier)], cwd="/app",
        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
    assert process.returncode == 86 and state(job)["version"] == 2
    job[1].materials.rename(job[1].materials.with_name("offline"))
    exact = PackagingDispatch(identifier, 1, "EXECUTE" if point == "after-dispatch" else "CLOSE")
    result = dispatch(job, exact)
    assert result.result.status == "RETRY_REQUIRED" and result.result.attempt == 1
    assert result.terminal == ("OPEN" if point == "after-dispatch" else "CLOSED")
    assert list(job[1].workspace.iterdir()) == [] and list(job[1].artifacts.iterdir()) == []
