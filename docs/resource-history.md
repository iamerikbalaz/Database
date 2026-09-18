# Ordinary resource history

Migration `20260918_0021` adds `resource_change_events`. It records ordinary
application create/PATCH transactions for published brands, projects, internal-user
profiles and PBR materials. Company history remains in its dedicated 0020 ledger.
Existing records get no invented baseline. A genuine no-op writes no event.

Each event carries one typed target foreign key, actor, per-target version,
creation/edit action, explicit before/after snapshots, SHA-256 digests and timestamp.
The event and domain write commit together. An audit failure rolls back the edit.
All four updates serialize under the target lock and existing authorization locks.
PostgreSQL validates exact current after-values, target binding, contiguous versions,
snapshot keys and action. It rejects history UPDATE, DELETE and TRUNCATE. ORM
mutations are rejected too. References cannot be cascade-deleted. This is application
and database-role protection; it does not claim to resist a database superuser.

## Read and inspect

Only ADMIN can GET `/api/{segment}/{id}/history`; segments are `brands`, `projects`,
`internal-users` and `materials`. Responses include `resource_kind`, `resource_id`,
at most 20 `items`, and `next_cursor`. Supply `?after=<event UUID>` for older events;
another target's or unknown cursor is rejected. Existing authentication, current
account authorization and no-store responses apply. Auditing an authorized edit
does not grant its author permission to read this administrator history.

Brand/project/material details show a collapsed history section. Account management
has one section per profile. Opening it loads history; older pages load only on
request. Actor, changed fields, before/after values and digests are inspectable.
Changing account, target or record version excludes late responses. There is no
restore button or automatic write. Snapshot values render as text.

## Coverage boundaries

- Brand snapshots exclude the system-managed next sequence number. The number
  reservation ledger remains authoritative for those allocations.
- User snapshots contain only ID, display name, email, role and active flag.
  Passwords, hashes, tokens, sessions and arbitrary credential metadata are excluded.
  Account access provisioning/reset and self-service password changes currently
  have separate sanitized operational logging, not this immutable profile history.
- Material snapshots cover the ordinary editable record. Specialized worker,
  workflow/review, identity, content, import, AI and publication operations retain
  their existing histories. A later ordinary event captures its actual starting
  state, which can differ from an earlier ordinary event after a specialized action.
  This endpoint is not a complete activity feed or a current-state assertion.
- Historical import, bootstrap and direct database maintenance are not silently
  relabeled as ordinary application create events. Legacy CRUD requests still have
  no exact idempotency replay contract.

## Migration and verification

No production migration has been performed. Upgrade from 0020 is additive and
preserves existing rows. An empty history table can downgrade to 0020. Once history
exists, downgrade refuses to discard it; use a reviewed forward migration. Do not
delete evidence to force a rollback.

Actual isolated PostgreSQL verification passed **355 tests**, including 41 new
immutability, malformed insert, concurrency and prior-schema upgrade/downgrade
cases. Auth gate: **27/27**, no skips. Local API/access/schema checks: **66 + 86
passed**. UI client/component/account checks: **101 passed**; full frontend:
**761 passed**, lint/build and E2E TypeScript passed. Full Linux backend:
**1858 passed**, no skips. Browser: **19 fresh + 19 retained passed** after fixing
real mobile overflow of long detail identifiers/names. The test retains its long
values and no-overflow checks. All four mobile history screenshots and desktop
project history were visually inspected. Consult the progress document for exact
run identities and the initial failing browser attempt. No external service or
production source was accessed.
