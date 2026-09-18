# Material archive and restore

Implemented on the autonomous development branch, with additive migration
`20260918_0023`. The handoffs name soft-delete/restore without defining cascading
or external-file behavior. This conservative local lifecycle keeps ownership,
evidence and files intact. It has not been deployed to the original/demo database.

## Commands and current-state binding

ADMIN-only preview/read/commands for one PBR record. Use a separate bounded archive
list/detail namespace (`/api/material-archives`) to avoid shadowing ordinary material
UUID routes. Ordinary material lists and source/work endpoints exclude archives.
Archived details and lifecycle history remain explicitly available to ADMIN.

Preview returns the current lifecycle version and an input digest bound to material
identity/assignment/ordinary fields, updated time, review generation and archive
state. The command requires that version/digest, a nonzero actor-scoped request key,
reason and explicit acknowledgment. Recheck current authorization and the input
under the material row lock, then commit lifecycle state, invalidation and immutable
event atomically. No external IO is needed for either command.

Exact replay/recovery checks the original actor/key/target/body before current
record/input checks, because a successful archive makes ordinary detail unavailable.
Replay never repeats the transition after a later restore or edit. A different
body with the same key conflicts. A missing recovery result does not prove an
in-flight request cannot commit. No browser automatic mutation retry or secret
persistence; use explicit exact retry or read-only recovery.

## Eligibility and effects

- Archive only `NOT_PUBLISHED` with `is_published=false`. Uploaded, pending/import,
  published and ambiguous publication/error states need a separate external-state
  reconciliation contract and are rejected initially.
- Both archive and restore hold the material lock and require no active/unresolved
  identity, packaging or staging owner. A timeout does not release ownership.
- Any recorded storage dispatch also blocks the transition, including an abandoned
  job whose local owner was released. Local closure does not prove external files
  are absent. Until a separate external-state reconciliation contract exists, there
  is no archive override. A closed reservation that was never dispatched is allowed.
- Preserve the row, UUID, brand sequence reservation, technical identity, relative
  source folder, assignment, content, all immutable histories, accepted packages,
  exports and cloud references. Never recycle a number or source identity.
- Archive/restore invalidate current review/technical/content readiness. A prior
  DONE record returns to IN_PROGRESS; IN_PROGRESS remains unfinished. The actual
  main schema has no NOT_STARTED state.
  Reset the current metadata pointer/readiness as reopening does while preserving
  immutable snapshots. Restore does not restore approvals or assert NAS availability.
- No cascade archive to company/brand/project or assigned accounts. No rename,
  file deletion, remote cancellation, cloud deletion or publication command.
- Restore preserves assignment; subsequent normal authorization/validation still
  applies. It does not silently activate an account or assign a different processor.

## Data and read boundaries

Use an additive migration with an explicit per-material lifecycle state (archive
flag, change time and version), plus append-only lifecycle events. Absence of a state
means active version zero; do not invent past events for old rows.
Typed columns and whitelisted command
results must bind action, target, actor, reason, versions and digests. State changes
and events commit together; no invented history for old rows. PostgreSQL enforces
valid transitions/event ordering and rejects evidence mutation. Populated downgrade
must refuse to erase history or archive state.

Keep existing accepted-package downloads and immutable CSV exports readable under
their existing roles, including stream reauthorization. Add narrow historical-read
exceptions rather than a blanket archived-material bypass. New source previews,
workers, approvals, packages, AI service requests and staging must reject archives
before IO and at acceptance. Late factual observations of an already-owned operation
must remain persistable; active ownership prevents archive in the first place.

The detailed integration map is in [the lifecycle plan](resource-audit-plan.md).
Test every direct material-load path and post-IO guard, exact replay after later
restore/edit, actual PostgreSQL races, prior-schema preservation/rollback guards,
unchanged source/identity/number/package evidence, and browser fresh/retained flows.

## Operator flow and recovery

An administrator opens **Archive and restore** on a material, selects **Review
archive**, supplies a reason and explicitly acknowledges clearing current readiness.
The saved record appears under **Archived materials**. **Review restore** uses the
same checks and confirmation. Current state must be refreshed separately from an old
command result; a recovered event describes that command, not all later changes.

The browser retains one unresolved packet per actor in memory across panel closure
and navigation. An uncertain response offers **Check saved lifecycle result** or
**Retry exact lifecycle request**. Neither runs automatically. A later denial or
missing result does not silently discard an uncertain packet. Leaving a page with
the lifecycle panel warns while that actor has a retained packet, even if the panel
is collapsed. Other pages do not install this warning. Packets are not persisted in browser storage. After a
full browser restart, read current state and history before considering new work.

Accepted packaging history/detail/dispatch reads and proof downloads retain their
existing authorization. Archived history is explicitly marked in the API; the UI
hides new packaging actions and clears the material from storage-upload selection.
Ordinary material history is a separate ADMIN ledger, still readable after archive.

## Migration and rollback

Apply the normal migration chain only to a separately authorized target. Migration
0023 adds state and event tables; it neither edits old rows nor invents prior
events. A deferred composite foreign key requires each state version to have an
exact event in the same transaction. PostgreSQL enforces ordered transitions,
review invalidation, unpublished/idle eligibility and immutable evidence.

An empty 0023 can downgrade to 0022. Once lifecycle evidence exists, downgrade
refuses: use a forward fix. Do not roll the application back to a version that does
not enforce archive state while retaining populated lifecycle tables; that version
would expose archived records as ordinary work. Restore selected records through
the reviewed application action when appropriate; never erase the event ledger.

## Verification boundaries

Tests cover real authentication/CSRF, exact recovery after restore, changed inputs,
active owners and closed storage attempts, immutable artifact reads, source IO
invalidation, ordered pagination, PostgreSQL concurrency/immutability and upgrade
from 0022. Browser tests commit the real command and deliberately lose its response,
then check read-only recovery/exact retry, retained restart and identity preservation.
Current run results and exact image identities are in
[the progress checkpoint](autonomous-pbr-progress.md).

This is individual PBR record archiving. Company/brand/project cascade deletion,
source cleanup, external publication withdrawal and storage reconciliation are not
implemented by this feature. No NAS, GCS or Notion call occurs during archive/restore.
