# Recoverable local packaging execution

The Linux packaging_execution module connects approved-input staging, real
ImageMagick conversion, all planned ZIPs and durable artifact retention. This is an
internal building block used by the opt-in [private service](packaging-service.md)
and [application actions](packaging-actions.md). Job reservation, current-account/
approval checks, ordered dispatch and operator controls are implemented by those
layers. This module cannot publish a material or contact an external service.

## Frozen request and caller responsibility

prepare_packaging_request validates the full reviewed report and canonical source
inventory, independently supplied technical-report hash (report without inventory),
whole material identity, explicit saved ZIP policy and storage timezone. The request
also freezes an approval-context digest and byte/time limits. The backend derives
that context from the batch, item, current approvals, material revision and saved
policy decision; see [reservations](packaging-reservations.md). Supplying a matching
hash is not authorization.

The caller must hold durable material ownership and freshly authorize initial
execution and every explicit retry. It must separately check source/approval
freshness before accepting a result for publication. These database/API gates do
not exist in this module and must not be bypassed by exposing it directly.

ExecutionLimits defaults to 1800 seconds, 8 GiB staged input, 16 GiB generated maps/
manifests/ZIPs and 16 GiB retained output. Maximums are 3600 seconds, 256 GiB staged
and 128 GiB each generated/retained. Stage, converter, ZIP and retention components
also impose their own tighter bounds. The cumulative deadline is passed to each
component. Cleanup uses the existing bounded descriptor walk (50,000 entries,
depth 20); a timeout never authorizes deletion of an unowned tree. Deployment must
also cap disk/memory/process counts and concurrent executions.

## Durable ownership and attempts

Configure four absolute, disjoint paths: the read-only materials root and existing
private 0700 workspace, artifact and execution-journal roots. All private ancestors
are opened without following links. The journal binds root paths by hash, their
device/inode identities, its own operation-directory identity, and the immutable
request. This prevents a changed root from silently becoming the old operation.

An operation UUID has an exclusive private journal directory and a nonblocking
execution lease. Its persisted device/inode/ctime identity must match on reuse.
Actual image probes and conversion descendants inherit this lease (see
packaging-lease.md); a killed supervisor cannot let recovery clean files still in
use by its child.

Each attempt first reserves a deterministic private workspace name, then records
its device/inode durably before any staging/conversion begins. The existing
packaging-UUID and artifacts-UUID directories live inside that recorded root.

Transitions:

1. RESERVED: request and lease are durable, workspace ownership is not yet recorded.
2. WORKING: the attempt workspace identity is durable; copying/conversion may start.
3. RETAINED: verified retained output and its proof digest are durable.
4. READY: retained output verifies and the attempt workspace has been removed.
5. RETRY_REQUIRED: known incomplete work has been reconciled and its workspace removed.

Up to 32 attempts retain their workspace identities and cleanup status. Artifact
retention has its own attempt/proof history; execution attempts before retention
do not invent artifact proofs. After a handled component failure, execution attempts
the same guarded cleanup used by recovery, while still holding the execution lease.
It only enters this cleanup after workspace ownership was durably recorded. The
original bounded error is returned if cleanup succeeds; an ownership/cleanup failure
is reported and ambiguous files are preserved.

The execution journal deliberately keeps WORKING or RETAINED until explicit
reconciliation, even when that workspace is already absent. An exception can occur
after retained output reached disk, so it cannot establish RETRY_REQUIRED or justify
deleting a retained result. Cleanup uses a detached record and does not rewrite
completion evidence. Reconciliation establishes READY versus RETRY_REQUIRED and
records cleanup. Process death still follows the crash recovery path below.

This removes temporary attempt work after ordinary staging/conversion/retention
errors; it does not expire accepted retained ZIPs or partially retained output.
That separate lifecycle must preserve historical proof and fence downloads/uploads.

## Replay and recovery

execute_packaging with an existing request reconciles the saved work and returns
READY or RETRY_REQUIRED. It never starts another attempt merely because a response
was lost. A caller's explicitly authorized retry=True can start a new attempt only
after successful reconciliation and revalidation of the same approved inputs.
Any changed report, plan, policy, approval context, timezone or limits requires a
new operation. Current source bytes are rechecked before and after staging.

reconcile_packaging needs no report and does not open, stat or resolve the materials
root. It checks retained files against their proof, completes interrupted retention
where possible, and removes only the recorded attempt workspace. Thus a completed
result can be recovered while NAS is offline. Completion after the artifact rename
but before the final execution journal write is recognized without rebuilding ZIPs.

Recovery refuses and preserves unknown journal files, unrecorded reservation gaps,
replaced roots/locks/workspaces, unexpected top-level workspace children, corrupt
artifacts/proofs and reappearance of already-cleaned workspace names. A complete
retained result is never downgraded to an incomplete retry after its bytes disappear.
Symlink cleanup never follows the target. Ambiguous storage needs operator review;
there is no automatic delete-all/retry shortcut.

## Verification

Tests run in the isolated opt-in Linux ImageMagick image with synthetic inputs,
no network, no database/ports or host data mounts. The image includes a source snapshot.
They exercise actual conversion, lease propagation, exact replay with NAS removed,
source changes, conflicting requests, private/root ownership, concurrent lease
contention from another process, proof corruption and guarded retry.

Real child processes deliberately exit between journal/ownership writes, during
input copying/retention, after retained rename and before/after cleanup/completion.
Unknown gaps remain preserved; known orphans recover and require an explicit retry.
Additional fault cases cover handled failures, byte limits, failure after durable
retention, immutable dispatch fences and preserved unknown/replaced workspaces.
Current complete-suite counts and immutable image identity are recorded in
[the progress checkpoint](autonomous-pbr-progress.md); earlier run evidence is
preserved in [historical checkpoints](autonomous-pbr-history.md).
