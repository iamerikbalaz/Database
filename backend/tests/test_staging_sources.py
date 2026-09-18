"""Owned synthetic source streams; no NAS, database or cloud connection."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import anyio
import pytest

from app.gcs_batch import compile_staging_plan
from app.gcs_contract import GcsError
from app.publication_staging import PreparedStaging
from app.staging_sources import StagingSources
from test_gcs_batch import inputs
from test_packaging_downloads import DATA, FILE_PATH, DownloadStub


async def authorized(): pass


def setup(*, guard=authorized):
    values = inputs()
    prepared = PreparedStaging(compile_staging_plan(**values), values["csv_bytes"], values["packages"])
    download = DownloadStub()
    worker = SimpleNamespace(open_artifact=download.open)
    provider = StagingSources(prepared, worker, operation_guard=guard)
    spec = next(item for item in prepared.plan.specifications() if item.relative_path.endswith("/" + FILE_PATH))
    assert spec.size == len(DATA)
    return prepared, provider, download, spec


async def read(provider, spec):
    async with provider.open(spec) as source:
        return b"".join([block async for block in source])


def test_streams_exact_frozen_csv_and_package_file_without_inventory_or_conversion():
    prepared, provider, download, spec = setup()
    csv = next(item for item in prepared.plan.specifications() if item.relative_path == "publication.csv")
    assert asyncio.run(read(provider, csv)) == prepared.csv_bytes
    assert download.opened == download.closed == 0
    assert asyncio.run(read(provider, spec)) == DATA
    assert download.opened == download.closed == 1


@pytest.mark.parametrize("change", ["csv", "missing-package", "duplicate-package", "material", "snapshot", "proof", "report", "plan", "guard"])
def test_invalid_or_substituted_sources_fail_before_any_worker_read(change):
    prepared, provider, download, _ = setup()
    package = prepared.packages[0]
    if change == "csv": prepared = replace(prepared, csv_bytes=prepared.csv_bytes + b"x")
    elif change == "missing-package": prepared = replace(prepared, packages=())
    elif change == "duplicate-package": prepared = replace(prepared, packages=prepared.packages * 2)
    elif change == "material": prepared = replace(prepared, packages=(replace(package, material_id=uuid4()),))
    elif change == "snapshot": prepared = replace(prepared, packages=(replace(package, batch_item_sha256="f" * 64),))
    elif change == "proof": prepared = replace(prepared, packages=(replace(package, result=package.result.model_copy(update={"terminal": "CLOSED"})),))
    elif change == "report": prepared = replace(prepared, packages=(replace(package, report={}),))
    elif change == "plan": prepared = replace(prepared, plan=prepared.plan.model_copy(update={"sha256": "f" * 64}))
    with pytest.raises(GcsError):
        StagingSources(prepared, provider.worker, operation_guard=None if change == "guard" else authorized)
    assert download.opened == 0


@pytest.mark.parametrize("change", ["job", "binding", "path", "size", "sha", "bool-size"])
def test_selection_is_exactly_bound_to_the_reviewed_complete_plan(change):
    _, provider, download, spec = setup()
    updates = {"job": {"job_id": str(uuid4())}, "binding": {"binding_sha256": "f" * 64},
        "path": {"relative_path": "unplanned.zip"}, "size": {"size": spec.size + 1},
        "sha": {"sha256": "f" * 64}, "bool-size": {"size": True}}
    with pytest.raises(GcsError, match="^GCS_SELECTION_INVALID$"):
        asyncio.run(read(provider, spec.model_copy(update=updates[change])))
    assert download.opened == 0


@pytest.mark.parametrize("change", ["corrupt", "truncate", "oversize", "proof", "open-error", "late-error"])
def test_unverified_or_interrupted_artifact_cannot_deliver_a_complete_source(change):
    _, provider, download, spec = setup()
    if change == "corrupt": download.data = b"!" + DATA[1:]
    elif change == "truncate": download.data = DATA[:-1]
    elif change == "oversize": download.data = DATA + b"x"
    elif change == "proof": download.mismatch = True
    elif change == "open-error": download.failure = RuntimeError("SYNTHETIC-PRIVATE-WORKER-ERROR")
    elif change == "late-error":
        def fail(): raise RuntimeError("SYNTHETIC-PRIVATE-WORKER-ERROR")
        download.during = fail
    seen = []
    async def attempt():
        async with provider.open(spec) as source:
            async for block in source: seen.append(block)
    with pytest.raises(GcsError) as caught: asyncio.run(attempt())
    assert "PRIVATE" not in str(caught.value)
    assert len(b"".join(seen)) < spec.size
    assert download.opened == 1 and download.closed == (0 if change == "open-error" else 1)


@pytest.mark.parametrize("when", ["before-open", "after-open", "during-source", "source-eof"])
def test_current_authorization_is_checked_before_source_and_before_its_last_block(monkeypatch, when):
    import app.gcs_client as transport
    denied = when == "before-open"
    async def guard():
        if denied: raise RuntimeError("Synthetic revoked operator")
    _, provider, download, spec = setup(guard=guard)
    def revoke():
        nonlocal denied
        denied = True
    if when == "after-open": download.callback = revoke
    elif when == "during-source":
        monkeypatch.setattr(transport, "RECHECK_BYTES", 1)
        download.during = revoke
    elif when == "source-eof": download.during = revoke
    with pytest.raises(GcsError, match="^GCS_OPERATION_BLOCKED$"):
        asyncio.run(read(provider, spec))
    assert download.opened == download.closed == (0 if when == "before-open" else 1)


def test_provider_owns_detached_proof_and_report_copies():
    prepared, provider, _, spec = setup()
    prepared.packages[0].report.clear()
    assert asyncio.run(read(provider, spec)) == DATA


def test_consumer_failure_preserves_original_error_and_closes_the_source():
    _, provider, download, spec = setup()
    async def attempt():
        async with provider.open(spec) as source:
            assert await anext(source)
            raise RuntimeError("Synthetic consumer failure")
    with pytest.raises(RuntimeError, match="Synthetic consumer failure"): asyncio.run(attempt())
    assert download.opened == download.closed == 1


def test_active_cancellation_finishes_both_generator_and_worker_cleanup():
    _, provider, _, spec = setup()
    closed = []
    async def run():
        entered = anyio.Event()
        @asynccontextmanager
        async def opening(prepared, report, result, path):
            item = next(item for item in result.stored.payload.files if item.path == path)
            async def chunks():
                try:
                    entered.set()
                    await anyio.sleep_forever()
                    yield DATA
                finally:
                    # An iterator cancelled inside its own body must shield its
                    # own async cleanup. The provider separately owns/closes the
                    # worker context, where the real HTTP connection lives.
                    with anyio.CancelScope(shield=True):
                        await anyio.sleep(0)
                        closed.append("generator")
            try:
                yield SimpleNamespace(file=item, proof_sha256=result.stored.proof_sha256, chunks=chunks)
            finally:
                await anyio.sleep(0)
                closed.append("worker")
        provider.worker.open_artifact = opening
        async with anyio.create_task_group() as group:
            group.start_soon(read, provider, spec)
            await entered.wait()
            group.cancel_scope.cancel()
    asyncio.run(run())
    assert closed == ["generator", "worker"]
