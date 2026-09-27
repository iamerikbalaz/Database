# Material archive and restore

Implemented on the autonomous development branch, with additive migration
`20260918_0023` and the forward guard update `20260927_0030`. The handoffs name soft-delete/restore without defining cascading
or external-file behavior. This conservative local lifecycle keeps ownership,
evidence and files intact. It has not been deployed to the original/demo database.

## Commands and current-state binding

ADMIN-only lifecycle preview/read/commands remain compatible under
`/api/material-archives`. Archive now reuses the Materials list, filters, gallery,
property chooser and editable cells through `GET /api/materials?is_archived=true`.
`GET /api/materials/{id}?include_archived=true` serves the matching detail. Both
explicit archive reads require ADMIN; ordinary reads keep their previous scope.
Archived list/detail responses add `is_archived` and `archived_at`. Active responses
retain their previous shape, including immutable ordinary-command schemas and hashes.

Preview returns the current lifecycle version and an input digest bound to material
identity/assignment/ordinary fields, updated time, review generation and archive
state. The command requires that version/digest, a nonzero actor-scoped request key,
an acknowledgment and a bounded audit reason (server default when omitted). Recheck current authorization and the input
under the material row lock, then commit lifecycle state, invalidation and immutable
event atomically. No external IO is needed for either command.

Exact replay/recovery checks the original actor/key/target/body before current
record/input checks, because a successful archive makes ordinary detail unavailable.
Replay never repeats the transition after a later restore or edit. A different
body with the same key conflicts. A missing recovery result does not prove an
in-flight request cannot commit. No browser automatic mutation retry or secret
persistence; use explicit exact retry or read-only recovery.

## Eligibility and effects

- Manual Published is independent of Archived and is preserved. Stable
  `NOT_PUBLISHED`, `PUBLISHED_CURRENT` and `PUBLISHED_UPDATE_REQUIRED` states are
  eligible subject to the ownership and external dispatch guards below. Pending,
  uploaded/import and error states remain blocked. Review invalidation marks a
  published record internally as requiring an update; it never clears Published.
- Both archive and restore hold the material lock and require no active/unresolved
  identity, packaging or staging owner. A timeout does not release ownership.
- Any recorded storage dispatch also blocks the transition, including an abandoned
  job whose local owner was released. Local closure does not prove external files
  are absent. Until a separate external-state reconciliation contract exists, there
  is no archive override. A closed reservation that was never dispatched is allowed.
- Preserve the row, UUID, brand sequence reservation, technical identity, relative
  source folder, assignment, content, all immutable histories, accepted packages,
  exports and cloud references. Never recycle a number or source identity.
- Archive/restore preserve Status, Checked, Published and all current metadata
  values, including the current snapshot pointer. They invalidate internal technical
  review/validation evidence. Restore does not restore approvals or assert NAS
  availability; it leaves the user properties as saved.
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
exceptions rather than a blanket archived-material bypass. Administrators may
read archive previews, with authorization and source identity rechecked after IO.
Other source/work, approval, package, AI service and staging commands reject archives
before IO and at acceptance. Late factual observations of an already-owned operation
must remain persistable; active ownership prevents archive in the first place.

The detailed integration map is in [the lifecycle plan](resource-audit-plan.md).
Test every direct material-load path and post-IO guard, exact replay after later
restore/edit, actual PostgreSQL races, prior-schema preservation/rollback guards,
unchanged source/identity/number/package evidence, and browser fresh/retained flows.

## Operator flow and recovery

An administrator toggles the **Archived** checkbox in either the table or detail.
The client checks current eligibility, then sends one exact command; no reason
input or additional confirmation is shown. The archive date is visible in Archive.
Clearing the same checkbox restores the material. Audit history stays on the server.

ADMIN can edit Note, Project, Processor and Published while archived, with the same
optimistic timestamps, row ownership checks and ordinary-write receipts. Workflow,
Checked and identity changes require restore. Other roles retain their existing
archive visibility boundary. ADMIN may recover an already committed ordinary
receipt after archive; replay returns evidence without repeating a mutation.

One uncertain packet per actor stays in memory and blocks another lifecycle change.
The browser offers **Check saved lifecycle result** and **Retry exact lifecycle
request**, never automatic retries. Missing results or later authorization denials
do not discard the packet. Navigation and reload warn while a request is pending.
No request or private row data is persisted in browser storage.

Accepted packaging history/detail/dispatch reads and proof downloads retain their
existing authorization. Archived history is explicitly marked in the API; the UI
hides new packaging actions and clears the material from storage-upload selection.
Ordinary material history is a separate ADMIN ledger, still readable after archive.

## Migration and rollback

Apply the normal migration chain only to a separately authorized target. Migration
0023 adds state and event tables; it neither edits old rows nor invents prior
events. A deferred composite foreign key requires each state version to have an
exact event in the same transaction. PostgreSQL enforces ordered transitions,
review invalidation, inactive publication/idle eligibility and immutable evidence.
Migration 0030 updates only the lifecycle guard; it never rewrites existing rows.
Its downgrade refuses incompatible currently published lifecycle records.

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
