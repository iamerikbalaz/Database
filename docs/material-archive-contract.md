# Planned first material archive contract

This is the concrete next slice after account security history. It is not an
implemented or enabled archive feature. The handoffs name soft-delete/restore but
do not define cascading or external-file behavior. The conservative scope below
keeps ownership, evidence and files intact.

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
- Preserve the row, UUID, brand sequence reservation, technical identity, relative
  source folder, assignment, content, all immutable histories, accepted packages,
  exports and cloud references. Never recycle a number or source identity.
- Archive/restore invalidate current review/technical/content readiness. A prior
  DONE record returns to IN_PROGRESS; NOT_STARTED/IN_PROGRESS remain unfinished.
  Reset the current metadata pointer/readiness as reopening does while preserving
  immutable snapshots. Restore does not restore approvals or assert NAS availability.
- No cascade archive to company/brand/project or assigned accounts. No rename,
  file deletion, remote cancellation, cloud deletion or publication command.
- Restore preserves assignment; subsequent normal authorization/validation still
  applies. It does not silently activate an account or assign a different processor.

## Data and read boundaries

Use an additive migration with an explicit material archive flag/time and lifecycle
version, plus append-only lifecycle events. Typed columns and whitelisted command
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
Keep this feature disabled/unavailable until those enforcement paths are complete.
