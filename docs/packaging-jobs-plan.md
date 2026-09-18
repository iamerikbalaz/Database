# Packaging jobs: database integration design

Worker execution, private HTTP and the independently validating backend client are
implemented. This is the next additive slice, starting with migration 0017; all
committed migrations through 0016 remain immutable.

## Lifecycle

Separate reservation from actual worker dispatch. A short create call validates a
current approved publication-batch item and saved ZIP policy, obtains a pure worker
plan, rechecks under established locks and stores immutable execution inputs plus
RESERVED ownership. Preparing a worker plan performs no NAS mutation or reservation.
Exact replay returns the same execution/progress and never dispatches automatically.

An explicit run/retry/reconcile action then authorizes the real current account,
session and role, claims the execution's process lease, records its immutable
dispatch, commits and performs IO outside a database transaction. The frontend can
poll the known execution ID while this synchronous internal-worker call is pending.
An initial RESERVED execution can be closed without worker IO only if it has no
prior dispatch. This avoids an unrecorded background queue and ambiguous auto-start.

A PostgreSQL session advisory lease (no open transaction during conversion) should
serialize dispatch/reconcile for an execution while its backend process lives.
A crash releases this ephemeral DB lease; the worker's durable request and inherited
Linux execution lease still protect files if its conversion is running. SQLite
unit tests can use a process-local equivalent; concurrency claims require real PG.

## Schema

Use four explicit tables rather than overwriting execution inputs/history:

- material_packaging_executions: immutable operation UUID, publication batch/item,
  material/brand, saved policy decision, initiating actor/session reference,
  idempotency key/request hash, approved input snapshot/hash and prepared worker
  request/hash. Link batch/item and policy with same-material composite foreign keys.
  The cached report is reconstructible from immutable technical/inventory snapshots;
  never store service credentials or raw production metadata.
- material_packaging_dispatches: immutable ID, execution, sequential ordinal, actor/
  session reference, action (EXECUTE, RETRY, RECONCILE, CLOSE), idempotency key,
  canonical request hash, reason and creation time.
- material_packaging_observations: immutable ID, execution/dispatch, outcome
  (READY, RETRY_REQUIRED, UNCERTAIN, NOT_STARTED), validated worker result or fixed
  failure code, source/context and actor-current facts, proof hash and creation time.
  One observation per dispatch; a stale returning dispatch may record its factual
  result but cannot overwrite newer progress.
- material_packaging_states: execution/material identity plus mutable current
  status and last dispatch/observation references. Use composite foreign keys to
  keep references in the same execution; identity columns cannot change.

Statuses: RESERVED, RUNNING, RETRY_REQUIRED and RECOVERY_REQUIRED hold material
ownership. PACKAGED and REJECTED are terminal for this execution. A unique partial
index permits only one active packaging owner per material. PostgreSQL triggers
serialize on the material row and reject coexistence with active identity work.
Enforce this in both directions: identity INSERT/reactivation and packaging state
INSERT/reactivation. Preserve append-only inputs, dispatches and observations at
both ORM and PG levels; refuse DELETE/TRUNCATE of mutable ownership state as well.
Populated downgrade must refuse provenance loss.

State consistency must bind the latest observation to the latest dispatch.
PACKAGED requires a verified READY proof and current source/approval/account
checks; uncertainty never counts as failure or releases ownership. Closure after
a prior dispatch requires a known fenced READY/RETRY_REQUIRED worker outcome.
NOT_FOUND alone cannot prove cancellation of a previously sent HTTP request.

## Application gates

ADMIN/LEADERSHIP can reserve/run/retry/reconcile within the existing session/CSRF
contract. Closing an ambiguous or obsolete execution should be ADMIN-only and
require a reason plus proof that no worker still owns active temporary work.
Do not invent an unsafe force-release shortcut.

Extend require_material_idle, require_brand_idle and require_folder_idle to consult
packaging ownership as well as identity operations. Keep established lock order:
auth domain gate, material row, folder catalog gate, then brand rows. Inspect every
material/content/approval/catalog/identity mutation for correct use of these gates.
Historical downloads remain readable according to their own authorization rules.

Initial acceptance is deliberately conservative: batch item snapshot must still
equal current prepared publication facts, including inventory/check IDs. Require a
fresh batch after any changed input rather than modifying old CSV or snapshots.
Freeze the exact saved ZIP policy decision and approval context with the worker
request. Recheck account, approvals/context and fresh source inventory before new
execution/retry and before accepting PACKAGED. Recovery may record factual retained
output after revocation while withholding permission to accept/publish it.

Packaging never flips is_published, confirms online import, uploads to GCS or writes
Notion. Those are later separately authorized stages with their own provenance.

## Verification

New schema must pass fresh and prior upgrade, current/heads/check, constraints,
append-only/populated downgrade guards and actual PG races (same/different request
keys, overlapping executions, identity/catalog/content changes and account changes).
Unit/API tests cover immutable inputs, role/assignment/CSRF, timeout uncertainty,
explicit retry, stale source/approval/policy, lost response, bounded history and
no work on idempotent replay. Finish with synthetic real-worker and fresh/retained
browser flows when API/UI integration is present. Existing/protected databases and
NAS are never test resources.
