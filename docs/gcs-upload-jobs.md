# Durable staging reservations and remaining execution design

Migration 0018 and the reservation/history/unsent-close API are implemented following
the GCS transport and staging preview. Migration 0019 adds the verified durable
dispatch journal described below. Actual upload
dispatch is not yet implemented. No production writes are authorized or performed.
Existing migrations through 0019 remain immutable.

## Reservation and ownership

The immutable job and item records freeze the entire plan, batch, actor/session,
target and accepted packaging-observation IDs. Reservation reruns `prepare_staging`
and compares the reviewed plan hash. Actor-scoped idempotency serializes simultaneous
retries: the same request returns the original job's current history even after
closure or later material edits; a changed request under the same key fails.
All material locks are acquired in UUID order before folder/brand locks.

One active staging owner is allowed per material. PostgreSQL triggers serialize
direct claims against identity and packaging operations for the same material in
both directions. Application gates additionally protect brand edits and overlapping
source trees. These application checks are not a general database restriction on
arbitrary direct SQL edits to materials, brands or other materials' folder paths.
Historical accepted package downloads remain available during the reservation.

Deferred PostgreSQL constraints require complete matching items and owners at commit
and complete owner release with the exact immutable closure record. Job/item/closure
facts reject update/delete/truncate. Owners cannot be deleted, reassigned or
reactivated after release. A populated downgrade refuses provenance loss; use a
forward migration. Empty downgrade to 0017 and re-upgrade are tested. Non-PostgreSQL
mutations fail closed, except explicitly configured SQLite unit tests.

ADMIN and LEADERSHIP can use these session-authenticated routes; mutations require
CSRF and current authorization:

- `POST /api/publication-staging-jobs`: the full staging-preview selection plus
  `batch_id`, `idempotency_key`, `expected_plan_sha256` and a reason. Returns 201.
- `GET /api/publication-staging-jobs`: paginated history (20 by default, at most 50).
- `GET /api/publication-staging-jobs/{job_id}`: frozen counts, target and material
  bindings, plan hash and current reservation/closure history.
- `POST /api/publication-staging-jobs/{job_id}/close`: exact plan hash, request key
  and reason. Atomically records closure and releases every owner. Exact replay is
  safe; a distinct second closure request fails.

This slice creates no upload, download stream, NAS operation or publication change.
The existing closure endpoint is explicitly **before dispatch** and rejects any job
with a recorded dispatch. Its original payload, request digest and exact-replay
behavior remain compatible. History reads the persisted progress introduced by 0019.
The E2E configuration pins a synthetic bucket/prefix, GCS disabled and an empty token.

## Dispatch journal (0019)

Five tables separate ordered dispatch intent, per-object transfer intent, immutable
storage observations, immutable dispatch results and guarded current progress.
The first action is EXECUTE; subsequent actions are read-only RECONCILE. The database
serializes them on the job and enforces the exact predecessor, ordinal and plan hash.
Transfer intents must follow the plan's exact object order. The next object cannot
start until every preceding object has a VERIFIED observation. A completion marker
requires all planned objects first. Receipt validation binds the exact job, plan,
path, size, SHA-256, bucket, object name and positive signed-64-bit generations.

Deferred constraints require complete progress at transaction commit. A dispatch
without a result remains RUNNING; an uncertain result becomes RECOVERY_REQUIRED.
A VERIFIED result requires complete receipt coverage and a verified marker.
STAGED_VERIFIED additionally requires current inputs, actor and lease flags. These
flags are application checks, not proof that SQL itself contacted the cloud or
authorized an account. The application must reconstruct the exact manifest bytes
from those receipts before recording its intent and accepting readback.

Late observations/results can be recorded without replacing newer progress. Closure
freezes the current dispatch/result pointers and releases all owners atomically.
A closure record must explicitly say whether dispatch occurred. The existing
unsent-close endpoint cannot close dispatched jobs; a separate acknowledged abandon
action still needs implementation. Closure never claims remote cancellation.

0019 backfills RESERVED/CLOSED progress for existing 0018 reservations. Empty journal
downgrade preserves those old reservations and restores 0018 closure semantics.
Once dispatch provenance exists, downgrade refuses rather than deleting it. Database
guards reject direct update/delete/truncate of immutable facts and deletion of
progress. ORM guards also protect immutable facts and closed state in unit tests.

## Next: execution orchestration

The GCS-specific dedicated-session lease is implemented in
`backend/app/staging_dispatch_lease.py`; its shared mechanics and tests are described
in `docs/packaging-dispatch-lease.md`. It is not yet connected to an upload route.
Commit an immutable ordered dispatch before cloud IO, and hold this lease outside
database transactions. Verify both session identity and lock ownership before each
consequential write and before accepting a result. A stale process can leave remote observations,
but it cannot replace newer database progress. Release the physical advisory session
instead of returning its lock to the pool. Keep SQLite simulation explicit to tests.

Record per-object outcomes and generation-bound receipts as immutable facts, including
an uncertain result when a write's response is lost. An object's metadata or a 404
does not prove a previously dispatched writer is finished. Never persist resumable
session URLs, OAuth tokens or raw remote errors. Apply bounded error codes only.

The runner opens only approved retained artifacts using their complete package proof;
the CSV comes from the frozen immutable batch. Streams remain bounded end to end.
The independently verified source adapter is implemented in `staging_sources.py`
(`docs/gcs-staging-sources.md`); connecting it to durable dispatch is still pending.
Recheck the live source, current account/session and approved input context before
dispatch and before acceptance. No transaction spans network IO. Revalidate returned
receipts against the frozen plan even when the transport is injected in tests.

`reserved_staging` rebuilds the active job from immutable database records and returns
the exact recompiled plan, frozen CSV bytes and validated package inputs. It checks
current authorization, approvals, policy and destination while holding the usual
domain locks. Its internal ownership exemption is limited to the exact active job;
ordinary previews and packaging requests cannot borrow that exemption by supplying
its UUID. Closed, missing or reassigned ownership fails. This read-only helper does
not acquire the dispatch lease or inspect live source files; the runner must do both
and must finish the transaction before network IO.

An object intent must commit before opening its
source or making any cloud request. Record the completion marker as an additional
object intent/observation, with exact coverage of every planned receipt. A late fact
may be retained even after lease loss; only the current nonclosed dispatch with fresh
authorization and input checks may advance progress to STAGED_VERIFIED.

Initial recovery is deliberately read-only. A partially uploaded or uncertain job
does not silently replay writes; an explicit replacement uses a fresh job namespace.
Each network operation and the complete runner need bounded deadlines. Preserve the
full uncertain history if cancellation, lost ownership or an unavailable source
prevents acceptance. Closing a dispatched job must acknowledge possible late remote
side effects and use the same session lease; it cannot retain the current unsent-only
audit wording or imply that all cloud work has been cancelled.

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

Reservation checks include fresh/prior migration and Alembic parity, append-only and
downgrade guards, real PostgreSQL cross-operation races, simultaneous exact retries,
partial-commit rejection, current approvals and role/CSRF enforcement. Execution
still needs interrupted IO/lease loss, account revocation and changed source while
uploading, late-result handling, exact recovery and frozen receipt history. Use
owned synthetic infrastructure only.
