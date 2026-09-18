# Next implementation: durable staging jobs

This is the execution design following the implemented GCS transport and staging
preview. It is not a report of an implemented upload API. No production writes are
authorized or performed. Existing migrations through 0017 remain immutable.

## Reservation and ownership

Add a migration for immutable upload input records and per-material ownership. A
reservation must rerun `prepare_staging`, compare the exact reviewed plan hash and
freeze the entire plan, batch, actor/session, target and packaging-observation IDs.
Use actor-scoped idempotency: exact replay returns the original reservation even
after later edits; a changed request under the same key fails. One active upload may
own each material. Acquire all material locks in stable order before folder/brand
locks. Ownership must exclude identity operations, packaging and overlapping source
trees in both directions, including direct database inserts. Active brand/material
edits must respect the same owner. Never reactivate a terminal packaging execution.

Reserve/close an unsent job first, in one independently testable slice. A committed
job must have complete matching material owners, enforced with deferred database
constraints. Immutable inputs and audit records reject update/delete/truncate.
Populated downgrade must refuse provenance loss. Use a forward migration for fixes.

## Dispatch and observations

Commit an immutable ordered dispatch before cloud IO. Hold a dedicated PostgreSQL
session advisory lease outside database transactions; verify both session identity
and lock ownership before each consequential write and before accepting a result.
Use a GCS-specific lock namespace. A stale process can leave remote observations,
but it cannot replace newer database progress. Release the physical advisory session
instead of returning its lock to the pool. Keep SQLite simulation explicit to tests.

Record per-object outcomes and generation-bound receipts as immutable facts, including
an uncertain result when a write's response is lost. An object's metadata or a 404
does not prove a previously dispatched writer is finished. Never persist resumable
session URLs, OAuth tokens or raw remote errors. Apply bounded error codes only.

The runner opens only approved retained artifacts using their complete package proof;
the CSV comes from the frozen immutable batch. Streams remain bounded end to end.
Recheck the live source, current account/session and approved input context before
dispatch and before acceptance. No transaction spans network IO. Revalidate returned
receipts against the frozen plan even when the transport is injected in tests.

## Recovery, closure and finalization

Read-only reconciliation checks every expected object through full readback. It does
not silently initiate uploads or infer absence of all side effects from a timeout.
An explicitly closed/abandoned job keeps its isolated namespace permanently separate;
a replacement uses a fresh job ID. Closing cannot promise that an already dispatched
remote request will not finish later. Persist late observations without reaccepting
the job. Never delete sources or remote data as an automatic recovery shortcut.

Generate and upload the completion manifest only after exact receipt coverage and a
fresh authorization/input/lease check. Its own upload and readback must also be
recorded. A marker alone never authorizes publication: acceptance additionally requires
the current durable job/dispatch and approved context. There is no atomic transaction
spanning GCS and PostgreSQL; each interrupted boundary must be recoverable without
pretending success or replacing newer progress.

The internal layout can reach **STAGED_VERIFIED** only. Reaching the product's
**UPLOADED_WAITING_FOR_CSV_IMPORT** requires an explicitly validated importer layout
and a verified complete batch. Recording the manual online import requires the
operator's explicit action and verifiable online IDs/date/hash; it never follows
automatically from preparing CSV, packaging, storage receipts or marker creation.

## Remaining product/operational gaps

The deployed importer layout and production golden artifacts remain unavailable.
The derived-file cleanup rule still needs an explicit lifecycle implementation:
temporary attempt workspaces must be cleared while preserving SOURCE/PREVIEW/master
and production metadata, immutable evidence and currently served historical downloads.
The existing retained artifact history must not be casually deleted or rewritten.
This gap is not closed by the transport or preview. Credential refresh/ADC, isolated
live verification, throughput and storage/readback cost also remain unverified.

Required next checks: fresh/prior migration and Alembic parity, append-only and
downgrade guards, real PostgreSQL cross-operation races, interrupted IO and lease
loss, account revocation and changed source, exact replay, frozen history and
retained-data browser scenarios. Use owned synthetic infrastructure only.
