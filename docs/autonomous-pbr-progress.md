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
reopen reason and recent review history. The approval slice below builds on it.
Verification so far: full isolated Docker backend 493, PostgreSQL 82 (auth gate
27/27), worker 178, previous frontend 199 passed with no skips. Updated frontend
206 tests/lint/build passed. The later strict highest-resolution client check
passed all 20 client tests. Real isolated E2E passed 9/9 on fresh data and 9/9
after restart with retained data (run 110d45d7). The new scenario verifies real
worker inventory, invalidation after a material edit, audited reopen, preserved
metadata history and the same revision after restart. Own resources were cleaned
up and the exact own volume retained. Main was rechecked at 88a1f99 and its
original working tree is clean.

## Completed slice: read-only technical image validation

The worker now actually decodes bounded master images and checks canonical map
names, duplicates, dimensions, required COL and 16-bit shortcuts against a full
unchanged source inventory. Metadata issues remain explicit nonblocking warnings.
The internal endpoint returns facts and findings only; human approvals are separate.
Resource limits and snapshot limitations are in `docs/technical-validation.md`.
Final isolated Linux worker run: 213 passed, no skips; two upstream test-client
deprecation warnings. This includes real PNG/JPEG/TIFF/WebP, invalid encodings,
source races, child diagnostics and rejection of overlapping validations. No
schema change or source filesystem write in this slice.

## Completed slice: persisted reports and server approval workflow

Migration 0008 adds append-only technical reports and technical/publication
approvals bound to generation and revision. Human approval requires DONE, a fresh
matching source check, and explicit acknowledgment plus note for warnings.
Technical errors block approval. Leads/admins approve technically; leadership/
admins approve publication. An approval never performs an external publication.
Identical checks preserve approvals; source/finding changes, mutations, reopen
and failed checks invalidate them without deleting history. See
`docs/material-approvals.md` for API, product assumptions and rollback.

Full own Docker run `reawote-test-e7f5b0d9513942c8af913ce07dc5f5d6` passed:
backend 527, PostgreSQL 90 (auth gate 27/27), Linux worker 213, previous frontend
206; lint/build passed, no skips. This includes fresh/prior-0007 migration and
concurrency with actual API edits/reopen/account disable during a delayed check.
An earlier local unit/API run passed 526 before the final stable-recheck test.

## Completed slice: technical review and approval UI

Material detail displays verified maps, concrete errors/warnings, reviewer/time/
note and separate technical/publication decisions. The UI requires explicit
warning acknowledgment and a note, retains retry keys for unknown network
outcomes and refreshes after rejection. Frontend 214 tests, lint/build passed.
E2E safety helpers passed 49 checks including guarded binary fixture writes.
All 10 actual E2E scenarios passed twice (run 6e80f4db), with the second pass
using the same database, files and accounts after service restart. The new
scenario uses generated 1K PNGs and verifies both approvals persist through
restart and unchanged revalidation. Only owned resources were cleaned up;
the exact own volume was retained and protected projects remained unchanged.

## Completed slice: controlled identity and filesystem operations

The planner, private durable journal and recoverable Linux executor now have an
explicitly authorized backend coordinator and detail-page UI. Migration 0009 adds
immutable number/history ledgers and durable operation ownership. Rebrands reserve
a target-brand number permanently before filesystem IO. Verified completion changes
the identity only after real source verification; interruptions retain ownership
for explicit recovery. Source writes remain disabled by default. See
`docs/identity-operations.md` for configuration, limitations and rollback.

Verification: backend 569, actual PostgreSQL 99 (auth gate 27/27), final Linux worker
287, frontend 224 tests passed with no skips. Frontend lint/build passed locally and
in Docker. Fresh/prior-0008 migrations, Alembic current/heads/check, allocation races,
account-disable races, immutable histories and active-operation downgrade refusal
passed. E2E safety helpers: 49 passed. The final real E2E run `f9f8e306` passed all
11 scenarios on fresh data and all 11 after backend/frontend/worker restart with
retained data. It verifies real source renames, unchanged UUID/project, target-brand
number, source hashes and history. Owned containers/network were cleaned; own DB
and per-run Linux source/journal volumes were retained. Protected resources remained
unchanged. Earlier E2E attempts exposed a selector mismatch (fixed) and one Chromium
ERR_NO_BUFFER_SPACE event; the final unchanged network-error gates passed both runs.
An earlier portable worker subset skipped 30 POSIX cases; full Linux verification
above supersedes that platform limitation. Two upstream Python test deprecations
remain. UI error focus timing was fixed and verified by the complete Docker suite.

The tracked `scripts/texture-zip.zip` contains both original historical packaging
scripts. Observed behavior is in `docs/legacy-packaging-contract.md`. Synthetic
comparisons are possible; production golden assets and online importer source
remain unavailable. This is still not the complete PBR product.

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

Identity worker/API/UI are committed in `4acc874`, `07ef4db` and `0b1e3ff`.
The next slice is normalized online categories, brand collections and versioned
publication content, followed by historical import and gallery/publication work.
Migration 0009 is committed: never rewrite it; add a new migration for new schema.

Docker runs through the local Linux engine at desktop-linux. Startup initially
failed on Windows error 1920 from stale zero-byte runtime socket reparse points.
Only verified runtime directories were reversibly renamed after stopping our newly
started Desktop processes. Retained quarantines are under
`C:/Users/Admin/AppData/Local/Docker/run.quarantine-reawote-01a0a64d` (and `-2`) and
`C:/Users/Admin/AppData/Local/docker-secrets-engine.quarantine-reawote-01a0a64d`.
No secret contents were read; no factory/WSL reset, data-directory move or volume
deletion occurred. Startup succeeded after both original runtime directories were
clear in the same stopped cycle. The full Docker and E2E tests above then ran.
Do not run standalone containers from Compose-built images during an E2E run:
Compose 5.4 image labels can make them appear to belong to that active project.
Use a separately owned project label or run sequentially.

`origin/main` was reverified via `git ls-remote`: still 88a1f99; original main clean.
Use this worktree and branch, inspect status and the latest commits, then continue
the unfinished step. Run tools here; never migrate/reset protected original DBs.
Set `E2E_PROJECT_NAME=reawote-e2e-auto-01a0a64d` for our isolated E2E runner. It
retains our own database and fresh per-run Linux identity volumes. Production NAS,
real GCS/Notion, backup and restore resources remain outside the allowed write scope.
Update this checkpoint with each completed slice and actual remaining limitations.
