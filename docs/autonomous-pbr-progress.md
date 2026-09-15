# Autonomous PBR completion

## Workspace and authority

- User authorized implementation, new migrations, isolated test resources,
  commits, own remote branches and a draft PR on 2026-09-15.
- Integration branch: `codex/autonomous-pbr-completion`.
- Dedicated worktree: `C:\Database\Database\tmp\autonomous-pbr-completion`.
- Starting commit: `88a1f99d748d2a0edbb1fce509e13d18bfc03908`, verified against
  GitHub. The original main checkout remains untouched.
- Do not inspect or reuse `feature/auth-ui`. Do not modify production NAS,
  backups, existing databases/volumes, main or live Notion/GCS data.

## Environment, 2026-09-15

The actual host has `C:\Database\Database`. Neither `C:\Databaze\Database`
nor the supplied `C:\REAWOTE-Backups\REAWOTE_2026-09-15_10-13-36` path is
available here. No backup content has been changed or restored by this task.

Docker Desktop is installed per user at
`%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin\docker.exe`, outside
the agent PATH. Docker CLI/Engine 29.7.2 and Desktop 4.88.1 work with the
`desktop-linux` context and local endpoint
`npipe:////./pipe/dockerDesktopLinuxEngine`. DOCKER_HOST, DOCKER_CONTEXT,
DOCKER_CONFIG, DOCKER_TLS_VERIFY and DOCKER_CERT_PATH were unset.

Protected runtime: `reawote`, `reawote-demo`, their volumes and existing
`reawote-e2e-postgres-data`. No task tests may reuse these resources or any
restore-test resources. Initial task baseline project is
`reawote-auto-baseline-01a0a64d`, with its own newly created volume.

## Baseline before changes

Read-only audit on Windows / Python 3.13.15:

- Backend: 438 passed; PostgreSQL: 68 skipped (20 auth, 48 materials).
- Worker: 71 passed, 58 POSIX tests skipped.
- Frontend: 178 passed; lint, TypeScript and production build passed.
- Orchestration mock: 8 passed; E2E helper checks: 46 passed;
  demo helper checks: 24 passed; direct-invocation guard passed.
- Full Docker baseline passed: backend 438, PostgreSQL 68 (including all 20
  mandatory auth tests), Linux worker 129, frontend 178; lint and build passed.
  No tests skipped. Own baseline database was stopped; its volume is retained.
- Backup restoration evidence from the user is not an application test.

## Confirmed defects to resolve

1. Ordinary Compose ports are not loopback-only by default.
2. Docker discovery/local endpoint validation is inconsistent between runners.
3. Worker accepts dimensions beyond backend/database precision and range,
   converting nonblocking metadata into a 503 on Done.
4. A DONE material can be relinked while retaining the old metadata snapshot.
5. Resource API is public, including user role changes. Forced password change
   and domain RBAC are not enforced. The frontend has no authentication UI.

## Product assumptions

- Retain the current technical identity, metadata.txt and ZIP boundary rules.
- Use least privilege where role permissions are unspecified; document the
  concrete matrix before enabling the corresponding endpoints.
- Unsupported metadata values should be omitted with a warning, without
  inventing precision or making metadata alone block Done.
- A different folder for DONE requires an explicit reopen workflow.
- Existing migrations are immutable; all schema additions use new revisions.

## Completed slice: local test isolation and material audit fixes

- Shared Docker discovery and local Linux endpoint validation for test/demo/E2E
  scripts; ordinary Compose ports bind only to 127.0.0.1.
- Full test runner always allocates a new `reawote-test-<GUID>` project, refuses
  collisions and pins its Compose/env files. Cleanup removes only that project's
  containers/network and retains its exact named volume.
- E2E can use `E2E_PROJECT_NAME=reawote-e2e-auto-01a0a64d`; it retains all prior
  ownership/mount/path guards. Do not use the protected default E2E volume here.
- Metadata dimensions outside positive Numeric(12,4) are omitted with warnings;
  raw bytes/hash and other valid fields are preserved, with no rounding.
- DONE rejects a different folder before calling the worker. Same-folder
  revalidation is idempotent and retains the snapshot.
- Verification: orchestration 8, E2E helpers 46, demo helpers 24 passed. Docker
  backend 439 and PostgreSQL 68 passed (auth gate 20/20, no skips). Linux worker
  145 passed after correcting two prior arbitrary-precision expectations.
  Frontend remains unchanged from its passing baseline; the stopped full run
  did not reach its frontend phase after the initial worker test failure.
- No schema changes in this slice. Revert the slice commit to roll back code;
  no original/demo/restore volumes were changed.

## Completed slice: server authorization and access recovery

- Every resource/material operation requires an active session, completed
  password change and CSRF + trusted origin for mutations.
- The role matrix and transaction/lock order are in `docs/authorization.md`.
  Processors see/work on assigned materials; leads manage production; leadership
  reads; only administrators manage accounts. Assignments require PROCESSOR.
- Session, role and assignment are rechecked inside the final write transaction,
  including after worker calls. Changing identity/role/active flag revokes
  sessions. A processor cannot inspect another identity through preflight.
- Admin credential provisioning/reset requires their current password, revokes
  target sessions and forces a new personal password. Host recovery CLI preserves
  roles and active flags. Self-demotion/deactivation is refused.
- Real PostgreSQL tests verify role/active/forced-change/revocation races, login
  waiting on account disable, actual authenticated concurrent Done and concurrent
  administrators trying to demote one another.
- Full isolated Docker run `reawote-test-a5d3d49b0ff649caab0982950b5b2dc4`:
  backend 459, PostgreSQL 75 (mandatory auth gate 27/27), Linux worker 145,
  frontend 193 passed; lint/build passed; no skips. Alembic fresh/prior upgrade,
  current/heads/check remain included in the PostgreSQL suite. Schema head 0006.

## Completed slice: authentication UI and account administration

- New UI implements bootstrap/login/logout, forced and voluntary password
  change, memory-only CSRF, stale-response protection and cross-tab refresh.
- Controls reflect server roles. Settings → Accounts supports creating profiles,
  changing role/active flag, provisioning/resetting temporary access and explains
  the required personal password change. Password fields clear after requests.
- Real isolated E2E passes all 8 scenarios, then all 8 again after restarting
  services against the same database/files/accounts (run e0f37e70). This includes
  account creation/reset/deactivation and server session revocation; worker →
  API → PostgreSQL metadata boundaries; persisted Done and one snapshot.
- Frontend: 199 tests, lint and production build passed. Demo auth 11, demo
  path/port 24 and E2E safety helper 46 checks passed. E2E trace is disabled to
  avoid storing cookies or auth request bodies.
- Demo seed now requires a real administrator login, trusted Origin and CSRF,
  and logs out in finally. The first-admin wrapper remains interactive. Only
  the demo overlay explicitly permits HTTP loopback cookies. Existing protected
  demo runtime was not started or changed; demo authentication helpers were
  tested with isolated mocks and actual auth HTTP is covered by E2E.
- No schema change. Reverting the UI keeps server authorization in place.

## Completed slice: source inventory and audited reopen

Worker inventory now hashes the complete source tree through no-follow Linux
descriptors, detects changes during scanning and fails without partial output.
Its contract and filesystem snapshot limitations are in `docs/source-inventory.md`.
Full Linux worker run: 178 passed without skips, including revision changes on
ZIP policy boundary and directory replacement. Earlier Windows run: 92 passed /
84 skipped (POSIX-only; two later POSIX tests were verified in Linux).
Persisted inventory and reopen are implemented with new migration 0007,
server role/assignment checks, idempotent mutations, immutable PostgreSQL audit
and invalidation on material changes. The detail UI exposes inventory, an explicit
reopen reason and recent review history. Technical/publication approval is next.
Verification so far: full isolated Docker backend 493, PostgreSQL 82 (auth gate
27/27), worker 178, previous frontend 199 passed with no skips. Updated frontend
206 tests/lint/build passed. The later strict highest-resolution client check
passed all 20 client tests. Real isolated E2E passed 9/9 on fresh data and 9/9
after restart with retained data (run 110d45d7). The new scenario verifies real
worker inventory, invalidation after a material edit, audited reopen, preserved
metadata history and the same revision after restart. Own resources were cleaned
up and the exact own volume retained. Main was rechecked at 88a1f99 and its
original working tree is clean.

## In progress

Next: technical image validation, technical approval and leadership publication
approval bound to both the observed revision and invalidation generation.
This intermediate version is not the complete PBR product.

## Remaining sequence

1. Baseline verification; safe isolated runners and local runtime checks.
2. Fix the four concrete environment/material defects above.
3. Server auth, role/assignment authorization, account provisioning/reset.
4. New session/login/logout/password UI and real authentication E2E.
5. Workflow, review/approval, reopen, inventory and invalidation.
6. Controlled identity/filesystem operations; categories/collections/import.
7. Gallery/comparison; publication, CSV and packaging.
8. Configurable GCS/Notion adapters, contracts, audit/soft-delete/restore docs.

Live external verification is blocked until separately authorized access and
test targets are supplied. Implementations must never simulate successful live
operations. 3D models and HDRI remain out of scope.

## Resume

Use this worktree and branch, inspect status and the latest commits, then
continue the unfinished step. Run tools from this worktree, never migrate or
reset the protected original database instances. Update this checkpoint with
each completed slice, actual tests and remaining limitations.
