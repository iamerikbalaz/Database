# Autonomous PBR completion

## Latest checkpoint (2026-09-18)

The owned branch is `codex/autonomous-pbr-completion`; the last verified main is
`88a1f99d748d2a0edbb1fce509e13d18bfc03908`. UI controls (`54aed25`), private
proof-bound worker downloads (`95e780b`) and authenticated application downloads
(`fe80a35`) are pushed. Downloads use browser-managed attachments for accepted PACKAGED jobs.
Historical packages remain readable after material edits. Current authorization is
rechecked between bounded blocks; no database transaction spans network IO. Upstream
connections close on cancellation and both transports verify size and SHA-256.

Final verification for the application-download slice:

- Backend: **1368 passed**, no skips (584.04s), isolated Linux image
  `reawote-download-backend-56639163886b4316ab354f6eb56e9b30:test`, manifest digest
  `cd54199277a2ea868001fffdc81ba5ad45174eee65ef13c99cc3dbc85862d8a5`.
- Actual PostgreSQL: **213 passed**, auth gate **27/27**, no skips (193.38s), project
  `reawote-test-3be74d7f9f604722b5956aafadac4712`. Includes revocation during opening
  and streaming, concurrent material edits, and checks for transactions held over IO.
- Frontend: **486 passed**; lint, build and E2E TypeScript passed.
- E2E `0822b9b3-ee49-455d-a198-999dd20f45e8`: **17 fresh + 17 retained passed**, including
  real ZIP downloads with length/hash verification after material/policy changes and
  restart. Desktop and 390px screenshots were visually inspected. Owned cleanup and
  protected-resource checks passed. Only explicitly owned test volumes/images remain.
- Worker is unchanged from `95e780b`: **710 passed** plus the actual production-runtime
  HTTP smoke, including offline NAS, restart, closure and retained-file downloads.

Existing dependency deprecations remain. No schema change was needed; migrations
through 0017 remain immutable. Live GCS/Notion, production throughput and the deployed
online-importer contract are unverified. No production resource was modified.
The configurable create-only GCS transport (`27ab34e`, pushed) is implemented with bounded resumable
uploads, full SHA-256 readback, conditional live-object verification, secret-safe
diagnostics and read-only reconciliation. It remains disabled and has no upload API.
The first targeted run found a forged boolean size being serialized as 1; strict
Python-model revalidation fixed it. Final targeted transport/config tests:
**158 passed**. Full isolated Linux backend: **1455 passed**, no skips, 558.72s,
two existing dependency warnings; image
`reawote-gcs-backend-431336568a1c442e8c7cb374520a668d:test`, manifest digest
`83276a2c868b34628b5099dec46e5db10dce38dbc4ba0103cefb0f13f83804f5`.
This image has no Compose labels; tests ran as UID 65532 without network, host
mounts, ports or DB, with a read-only root, bounded tmpfs and automatic container
removal. The test image remains. No live GCS operation occurred. Details and remaining
credential/importer limitations are in `docs/gcs-staging-transport.md`.
The internal batch compiler and authenticated current-input staging preview are now
implemented (`docs/gcs-batch-plan.md`). Every CSV row binds exactly one accepted
package; the plan includes all retained files. A deterministic completion manifest
requires exact receipt coverage. The preview rechecks current approval/policy/batch
bindings without cloud or NAS IO. It creates no upload job or publication transition.
The internal staging layout is explicitly not importer compatible.

After the 1455-test image, targeted Windows checks passed **80 tests** (19 new preview,
41 batch compiler and 20 access tests), then the final Linux affected-area suite passed
**364 tests in 173.18s**, no skips, two existing dependency warnings. Image
`reawote-gcs-staging-7bd431ca2402462c857cf526d446cfd7:test`, manifest digest
`428b05e9946942c02d1cb5a4abede4f5ca9f4a515af6d95b3f0a4d55efcbe365`;
no network, host mounts, ports or DB, read-only UID 65532, bounded tmpfs. The current
PostgreSQL suite passed **215 tests in 189.38s**, auth **27/27**, no skips, five warnings
(the two dependency deprecations plus Pydantic field-alias warnings): project
`reawote-test-4fd7fae12cc946b5867b7ab99b9511a5`. Actual concurrent material/brand edits
waited for preview locks and invalidated the next preview. Fresh/prior schema checks
also passed; there is no new migration. Owned cleanup completed; only its exact
`reawote-test-4fd7fae12cc946b5867b7ab99b9511a5_postgres_data` volume and test images remain.

Durable staging reservations, paginated history and atomic unsent closure are now
implemented with additive migration 0018 (`docs/gcs-upload-jobs.md`). Each job freezes
the reviewed complete plan and accepted package observations. Current ADMIN/LEADERSHIP
authorization and CSRF are enforced. Exact concurrent retries share one durable job;
owners exclude material/brand/folder edits and other application operations. Direct
PostgreSQL claims on the same material are protected in both directions. Deferred
constraints reject partial reservation/release, immutable provenance rejects mutation,
and populated downgrade refuses data loss. Closing releases owners without cloud IO.

Final reservation verification for 0018: **19 reservation API tests** (52.89s), **51 Alembic/metadata
tests** (25.01s), and **224 real PostgreSQL tests** (222.16s), auth gate **27/27**, no skips,
three existing warnings. PostgreSQL project `reawote-test-f3ea832532904c9eb3065ffd13733bde`
completed owned cleanup; its exact `..._postgres_data` volume remains. Fresh/prior
upgrade, empty downgrade/re-upgrade, Alembic parity, append-only guards, deferred
constraints and direct identity/packaging races passed. The final SQLite-test-only
guard was verified after that PostgreSQL image; it leaves PostgreSQL behavior unchanged.
The full Linux backend passed **1534 tests in 700.70s**, no skips, two existing
dependency warnings; image `reawote-staging-reservation-364cc99017c14efb979985523bfbcb08:test`,
manifest digest `f26faf32e4bd7da5741691da23cb3d406cddadc027a9912afac40eaa8c02819c`.
It ran as read-only UID 65532 without network, host mounts, ports or a database, with
bounded tmpfs and automatic container removal. The image remains.
E2E `f2e11caf-c82d-4487-abe6-7dbc5044b1f0` passed **17 fresh (1.2m) + 17 retained (42.5s)**
scenarios: actual accepted package preview, exact reservation replay, active edit
rejection, exact closure replay and frozen staging history after edits/restart. E2E
pins a synthetic destination with GCS disabled and no token; no live GCS operation
was tested. Owned cleanup and protected-resource checks passed; explicitly owned
test volumes and successful UI artifacts remain. Product UI was unchanged.
Frontend lint and E2E TypeScript checks passed; product frontend code is unchanged.

Reservations are committed as `8d86da5`; migration 0018 is now immutable. The next
transport slice adds per-operation async authorization/ownership checkpoints before
credential access, every HTTP request, bounded streaming progress and receipt return.
Revocation after a remote write returns no accepted receipt and preserves uncertainty.
The callback's private exception is reduced to `GCS_OPERATION_BLOCKED`; cancellation
still releases resources. This is a transport hook, not an implemented upload runner.
Targeted Windows GCS tests passed **149 tests (4.94s)**; the isolated Linux transport,
guard, batch and configuration suite passed **220 tests (4.11s)**, no skips, two
existing dependency warnings. Image `reawote-gcs-guard-b77b156c52f64845b5e8f061ef3ef265:test`,
manifest digest `66b14fcc650f4e1cb6394c6b393593a1d1298f91a5eccbefcb1b8eea1fd49893`;
read-only UID 65532, no network, host mounts, ports or DB, bounded tmpfs, auto-removed
container. This later image covers the guard changes after the 1534-test snapshot.
The reservation and transport-guard commits (`8d86da5`, `02dc096`) are pushed.
Remote main was rechecked and remains `88a1f99`.

The dedicated-session dispatch lease is now shared by the existing packaging
wrapper and a separate internal GCS namespace. Packaging keeps its exact original
namespace/error codes. Both domains verify PostgreSQL PID/lock ownership, refuse
silent reconnect, close the physical session on exit and keep SQLite test-only.
Local lease plus packaging action/reservation tests passed **69 tests (99.07s)**.
The fresh PostgreSQL suite passed **233 tests (231.46s)**, auth **27/27**, no skips,
three existing warnings. Project `reawote-test-95dc11bcc0964393bb2e7780868374e1`,
backend image manifest `b995f0c38660ed508fedee95a830bb410ede0e4cdd287d5b3c16f011200e3488`.
It covers independent sessions, actual child-process death, killed DB sessions,
unexpected unlock, cleanup on exceptions and namespace separation for the same UUID.
Owned cleanup completed; its exact `..._postgres_data` volume and image remain.
The GCS lease is a verified primitive; no upload route uses it yet.

Next: durable dispatch/recovery, then the manual-import workflow. No upload API,
actual importer mapping or GCS UI is implemented yet. Existing migrations through
0018 remain immutable. The older checkpoints
below record historical test counts and intermediate limitations.

## Workspace and authority

Current checkpoint (2026-09-18): publication preflight, immutable CSV batches and
selection/history/download UI passed the complete isolated suite in project
`reawote-test-2fd633be5ba54892a6638e9e4c2d9bf7`: 1100 backend, 163 actual PostgreSQL
(auth gate 27/27), 354 Linux worker, 436 frontend, lint/build; no skips. Additive
migration 0015 passed fresh/prior upgrade, Alembic current/heads/check, constraint,
concurrency and downgrade guards. E2E `d4c69daa-90c2-4679-b750-c3601d47c8a1` passed
17 fresh scenarios (1.0m) and 17 retained scenarios (36.8s), including lost-response
recovery and frozen downloads after content edits. Saved-batch desktop and 390px
screenshots were visually inspected; owned cleanup and protected-resource checks
passed. Backend/migration 0015 are committed and pushed as `dfe66c5`; migrations
through 0015 are now immutable. The tested UI is committed as `7337ac0` and the
pure packaging planner as `dec964e`; all three commits are pushed to the owned
branch. `origin/main` was rechecked on 2026-09-18 and remains `88a1f99`.
Subsequent local packaging now completes approved input staging, actual bounded
ImageMagick conversion, all planned resolutions, web manifest and verified ZIPs.
The final complete worker suite in
`reawote-packaging-e962d1e639424f4a88f3b24431408e7f` passed **516 tests in 127.99s**,
with no skips and two existing dependency deprecations. It includes 31 planner,
32 staging, 41 conversion, 35 ZIP and 23 complete assembly cases. The offline
packaging runner isolation suite also passed all 11 cases. The build ran as
UID 65532 without network, host mounts, databases or ports, with bounded tmpfs;
owned cleanup succeeded. Only its own test image is retained.
Staging `4656754`, conversion `1e86f63` and ZIP writer `5fb97a0` are pushed;
complete assembly is committed and pushed as `fc5358b`. See `docs/packaging-assembly.md`
and its related component documents for commands, limits and verification scope.
Durable local retention is implemented and verified. `docs/packaging-retention.md`
describes exact replay, guarded atomic completion, restart recovery and artifact
readers. Its initial 36 tests had six JSON container type failures (fixed); a later
source-name assertion was corrected. The final complete worker run
`reawote-packaging-5a97dd5f84c04a4b820c0c1280ad08a0` passed **562 tests in 207.71s**,
including all 46 retention cases, with no skips and the same two dependency
deprecations. The offline runner safety suite also passed all 11 cases again.
Owned cleanup succeeded; only the test image remains. No packaging HTTP endpoint,
live upload or deployment exists.

Retention is committed and pushed as `8810c0a`; main remains `88a1f99`.
The next slice implements first-policy persistence and administrator
preview/override with immutable migration 0016 (`docs/packaging-policy.md`). Full
isolated project `reawote-test-c20f7bc006b6437da53ac19b39e78cab` passed **1126 backend,
171 actual PostgreSQL (auth 27/27), 452 ordinary-worker and 436 existing frontend**
tests, lint/build, fresh/prior migrations and Alembic checks. Its ordinary worker
skipped 110 ImageMagick-dependent cases; all 110 were executed successfully in the
required-runtime 562-test packaging run above. Only existing dependency warnings
remain. Owned cleanup passed and its exact database volume is retained.
The subsequent policy UI passed **457 frontend tests** including 21 new cases.
Build, lint and E2E TypeScript passed after correcting test-only typing/lint issues;
server/migration 0016 are committed as `b93fd16`. Policy UI is committed as
`5e88f24`; both commits are pushed to the owned branch. Migrations
through 0016 are now immutable. First E2E run `2a41e17e-185d-414b-8a3b-3361f59cda0e`
passed 16 scenarios; publication/policy failed because the post-save callback
remounted the whole detail and hid the success notice/expanded panel. The new
panel now refreshes detail data in place. Its database save had succeeded; no
assertion or validation was weakened. Final E2E run
`403e4755-695e-4bda-969b-028a8ea64427` passed **17 fresh (1.1m) and 17 retained (41.4s)**
scenarios, including actual first selection, administrator preview/override, exact
retry after a lost committed response, preserved CSV and history after restart.
Desktop and 390px screenshots were visually inspected without overflow. The final
457 frontend tests, lint/build and E2E TypeScript check also passed. Owned cleanup
and protected-resource checks passed; only owned DB/source/journal volumes remain.

Execution prerequisites are in progress: `docs/packaging-lease.md` describes a
private lease retained by actual ImageMagick/probe descendants after supervisor
death. Its first 15 Linux tests passed in 1.81s without skips, including real
SIGKILL and ImageMagick exec inheritance. Complete required-runtime regression
`reawote-packaging-ed9ec1644c764477ab3008bc7ff19e6c` passed **577 tests in 204.51s**,
with no skips and the two existing dependency deprecations. Owned cleanup passed;
only its test image is retained. The verified lease slice is committed and pushed
as `5f43570`. Remote main was rechecked and remains `88a1f99`.

Recoverable execution now connects staging, conversion, ZIPs and retention
(`docs/packaging-execution.md`). Its first 38 isolated Linux cases passed; the
subsequent 12 limits/corruption/ownership cases also passed (15.54s). Nine cases
use real child-process termination, including copying, reservation gaps, retained
rename and cleanup transitions. Unknown ownership is preserved; known incomplete
work requires an explicit retry, and completed results recover with NAS offline.
The complete required-runtime worker run
`reawote-packaging-1e69c0b534a94f54ab7c542ec2874ede` passed **627 tests in 279.97s**,
no skips, with the two existing dependency deprecations. Owned cleanup passed;
only its test image is retained. This execution slice is committed and pushed as
`e1d108e`.

The opt-in private packaging service (`docs/packaging-service.md`) is implemented
separately from the ordinary worker. Its first 29 API cases passed in 27.55s,
including actual execution and lost-response recovery, strict service-token/body
gates and concurrency. Dockerfile.packaging now has a runtime target and a default
test target. The runtime-only image
`reawote-packaging-service-01a0a64d-20260918:runtime` passed actual Uvicorn/HTTP
smoke: synthetic rectangular conversion, retained proof verification, restart and
exact replay with NAS offline. It ran without network, ports or database; only
the synthetic smoke script was mounted read-only. The 11 offline runner safety
cases also passed. Complete worker regression
`reawote-packaging-73b27a2d799c40659379f478b8d489b6` passed **656 tests in 306.37s**,
no skips, with the two existing dependency deprecations. Owned cleanup passed.
The production-image smoke also exported a synthetic request/report/result for
independent backend contract tests; no credentials or source metadata are included.
An initial extraction from an already-stopped tmpfs failed; direct export to the
owned fixture succeeded in a subsequent passing smoke. API/image changes are
committed and pushed as `8abf702`.

The separate backend packaging contract/client passed its first 70 tests and then
73 tests (2.65s), including all frozen byte budgets, with no skips and the two
existing dependency warnings. Four synthetic fixtures were exported from actual
production-image HTTP/conversion/restart runs: rectangle, multiple resolutions
with 16-bit maps/current timestamps, nonstandard 3K master and square COPY. All
four passed independent backend proof validation. Rehashed corrupt layouts,
request substitution, unavailable credentials, response/body limits, lost network
responses and no-implicit-retry behavior are covered. The client remains unwired
until durable database ownership/current-account/approval checks are implemented.
Final archive-size parity checks passed in the same 73-case suite (2.57s).
The verified client/contract slice is committed and pushed as `0a97a6f`. No
database schema, application endpoint or frontend behavior changed in this slice.
The next integration design is recorded in `docs/packaging-jobs-plan.md`:
immutable execution/dispatch/observation history, separate mutable ownership,
explicit reserve/run/reconcile, and bidirectional exclusion with identity work.

Migration 0017 and the four provenance/ownership tables are implemented and
verified. Shared material, brand and
overlapping-folder mutation gates include active packaging. Twelve focused
ownership tests plus the migration-head test passed (13/13, 17.37s); the initial
failure was a stale test-only folder reference, corrected without weakening the
assertion. Fourteen new PostgreSQL cases cover competing owners/identity work,
immutable/deferred provenance, terminal states, input substitution and prior/empty
downgrade upgrades. Full isolated project
`reawote-test-09c7300533ae43e891b45c7c2ed35b7f` passed **1211 backend tests (396.72s),
185 actual PostgreSQL cases (153.75s, auth 27/27), 476 ordinary-worker and 457
frontend tests**, plus lint/build. The ordinary worker skipped 180 runtime-only
cases; all 180 already passed in the unchanged 656-case required-runtime suite
above. Fresh/prior migrations, Alembic current/heads/check, real competing owners,
identity exclusion and downgrade guards passed. Owned cleanup succeeded; only its
database volume is retained. No application packaging endpoint or UI dispatch is
enabled by this schema slice. Committed migrations through 0017 are immutable.
The schema/ownership slice is committed and pushed as `1dc5835`.

Backend dispatch coordination is verified separately (`docs/packaging-dispatch-lease.md`).
Ten focused tests passed; isolated PostgreSQL-only project
`reawote-test-19a24c2d30694e219c36d11739c0f509` passed **193 actual PG tests (158.37s,
auth 27/27)**, including eight new real lease/process-death cases, without skips.
The dedicated session is idle outside transactions, excludes independent backend
engines, detects unexpected disconnect/unlock, and physically closes on every exit.
The test runner now supports PostgreSQL-only work with the same isolation/auth
gates; all ten offline runner safety cases passed. Owned cleanup passed and its
database volume is retained. Reservation/API integration is now in progress and
has not yet been verified or enabled.
The verified lease/runner slice is committed and pushed as `ff4010b`.

Reservation and unsent-closure API implementation is in progress
(`docs/packaging-reservations.md`), disabled by default. Its first combined local
API/configuration/access suite passed **113 tests (57.04s)** and the additional
packaging configuration suite passed **29 (0.82s)**. Nine real PG API concurrency
cases are added but not yet executed. Current work does not dispatch conversion,
upload artifacts or publish materials; these are subsequent integration steps.
The first full reservation run in `reawote-test-4793d5b2e1594329b004cf93427f6ddb`
passed 1270 backend tests and failed two default-configuration tests (448.30s).
Compose intentionally supplies empty credential environment variables; those two
tests had assumed the environment was unset. They now explicitly clear only the
settings whose defaults/independence they test. No application authorization check
was changed. The runner correctly stopped before PG/later phases and cleaned its
own resources. Corrected container configuration tests and the new PG cases remain
to be run. Source/client code and the 1270 passing backend cases are unchanged.
Follow-up verification now passed: **100 configuration tests (1.87s)** in the
rebuilt isolated image with the same empty Compose credential variables and no
network/database; and **202 actual PostgreSQL cases (169.30s, auth 27/27, no
skips)** in `reawote-test-eeec858c7c5c4ffd9102bd9cc50f4459`, including all nine new
API races. Owned cleanup succeeded; its exact database volume is retained.
The first full run's two test-only environment assumptions are resolved. No
frontend source changed; the preceding 457 frontend/lint/build result remains
applicable. Ordered worker-command fencing is now being implemented separately;
application conversion dispatch remains unavailable until that boundary is ready.
The verified reservation/API slice is committed and pushed as `5e3ac9e`.

Ordered worker dispatch fencing is implemented and verified
(`docs/packaging-dispatch.md`). The complete Linux runtime suite in
`reawote-packaging-da8cb1025e03461cbc7ef339e972b35c` passed **687 tests (338.45s)**,
without skips and with two existing dependency warnings. Owned cleanup succeeded.
The final backend contract/ordered-client run passed **94 tests (2.74s)**,
including independent validation of the actual exported ordered proof fixture.
Runtime image `reawote-packaging-dispatch-c554f7444f40495bab148b22912188b4:runtime`
passed actual HTTP, 2K/1K and 16-bit conversion, offline recovery, permanent closure
and two service restarts, including rejection of stale/new/legacy retries after
closure. It exported the synthetic ordered proof fixture in the backend tests.
Application run/retry/reconcile integration remains the next step;
production publishing and external writes remain
outside this run's authority.

The verified ordered worker/client slice is committed and pushed as `c4b23b9`.
Explicit application run/retry/reconcile and post-dispatch closure are now
implemented but under verification (`docs/packaging-actions.md`). The first
combined local API/ownership/publication/access suite passed all 100 cases.
New fixture setup initially omitted source-warning acknowledgment, JSON enum
parsing, timezone/policy rebinding and deterministic latest-observation lookup;
these test-only issues were corrected without weakening production validation.
Final local action/ownership checks passed **38 tests (66.11s)**, including the
persistence bound, suppressed serializer payload warnings and lost-lease handling.
Actual PostgreSQL project `reawote-test-6c48d5726a264ddba2c8e306d66731bd` passed
**210 tests (182.01s, auth 27/27, no skips)**, including eight new concurrent
dispatch/revocation/acceptance/stale-result/offline-closure cases. Owned cleanup
succeeded; its exact database volume is retained. The final JSON persistence
bound/serializer warning guard was added after that image build. Full final
backend regression in `reawote-packaging-backend-fe048c33396f4bbd8b8dcc53861b9f82`
passed **1319 tests (525.51s)** with no skips and the two existing dependency
warnings, including those guards. It ran without network, database, ports or host
data, as UID 65532 with a read-only root; its container was removed automatically.
The application action suite is complete. No committed migration was modified.
The verified application action slice is committed and pushed as `da6d7ce`;
remote main was rechecked and remains `88a1f99`.

Packaging UI/client is implemented: lazy material/batch
panels, separate reserve/start, exact recovery of a lost response, progress polling,
role-specific retry/reconcile/closure and bounded action history. All **476 frontend
tests (17.21s)** passed, including 19 new cases; lint/build and E2E TypeScript passed.
The isolated E2E environment now includes a private, non-root packaging service,
read-only synthetic sources, its own fresh retained volume and internal network.
All 16 new packaging isolation guard checks and existing E2E helper checks passed.
E2E `13b30160-f270-4f26-a25e-f3d3f6f60845` passed **17 fresh (1.2m) and 17 retained
(38.0s)** scenarios with actual local packages, exact recovery after a lost run
response, a single READY dispatch and explicit unsent closure. A later run
`6a5e1dec-d5b0-4d7e-a8b6-759a48f085c7` was safely stopped by the exact runtime mount
guard before browser work; owned cleanup succeeded. The guard was split into
specific diagnostics without relaxing its comparisons. The failure did not recur
in final E2E `5378cfc9-3b40-48e5-a16d-6b6575d5094d`, which passed **17 fresh (1.2m)
and 17 retained (39.8s)**. Its desktop/390px packaging screenshots were visually
inspected; no overflow was present. Duplicate action-list numbering was corrected.
The final 29 affected frontend tests and lint/build/E2E TypeScript also passed
before that cosmetic numbering change; all 19 packaging UI tests passed again
after it. All 16 packaging guard checks passed again.
The final cleanup removed only owned containers/networks and preserved owned
DB/source/journal/packaging volumes; protected resources were unchanged.
No production Compose service or live deployment was added.
The verified UI/E2E slice is committed and pushed as `54aed25`; the remote main
was rechecked and remains `88a1f99`.

Private proof-bound artifact downloads now pass actual production-image HTTP,
offline-source recovery, two restarts and ordered closure checks in
`reawote-packaging-service-8fe7891eb5674acab8d23f089aa3adbe:runtime`.
The complete required-runtime Linux worker suite in
`reawote-packaging-fcd011409d0241bf8df2ec289fb95468` passed **710 tests (379.66s)**,
including 23 new download cases, with no skips and two existing dependency
warnings. The initial focused download/retention run had 66 passes and three
failures: request/plan conflicts were incorrectly mapped to 503, and ASGI 2.4
disconnect exceptions were not suppressed. Both were corrected without weakening
assertions; all cases passed in the final suite. Descriptor/operation/service
leases close after cancellation, failed sends, timeout, corruption and replacement.
Owned cleanup succeeded; only the test/runtime images remain. The tracked
`scripts/test-packaging-service.ps1` reproduces the real HTTP smoke without fixture
exports, DB access, host ports or source mounts (`docs/packaging-downloads.md`).

Next: proof-bound artifact downloads. Durable jobs, current approval/source checks
and operator UI are implemented (`docs/packaging-actions.md`). GCS/Notion,
online-import confirmation and the remaining catalog, history and operations work
are unfinished. Older entries below are history; this checkpoint takes precedence
over their former pending states.

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

Catalog server/schema are committed in `eda1767`, UI in `c96baa6`. Migration 0010
is now immutable. The slice adds normalized vocabulary, versioned material drafts,
immutable history, role/assignment checks and approval invalidation. Catalog names
and collection brand ownership are immutable; availability changes are audited.
The complete isolated Docker run `reawote-test-6d760c56aa7f44a1ad1b5fc1ed574357`
passed: backend 592, PostgreSQL 106 (auth gate 27/27), Linux worker 287 and frontend
240, all without skips. Frontend lint/build passed in Docker and locally. Own
containers/network were cleaned; the exact own database volume was retained.
Warnings: two upstream Python test deprecations and one Pydantic/FastAPI query-alias
warning during PostgreSQL checks. Fresh/prior-0009 migrations, current/heads/check,
concurrent catalog creation/retirement/content saves and DB immutability passed.

E2E helper safety checks: 49 passed. Final E2E run `90325fd0-3a35-4074-96e4-9bef4954194a`
passed all 12 scenarios on fresh data and all 12 after application restart with
retained data. The catalog scenario verifies revision 2, history, normalized tags,
category retirement/reactivation and approval invalidation. Earlier attempts
exposed a filled-textarea label selector mismatch (fixed using its textbox role)
and pending response bodies in existing Done tests. Those tests now await all
detail panels before reload and report unfinished endpoint paths after a bounded
diagnostic wait. All response leak, console and network-error gates remain enabled.
The earlier sequential run also reproduced the timeout, so parallel execution
alone was not its cause. Own containers/network were cleaned; own DB/source/journal
volumes retained, protected resources verified unchanged.
An earlier uncommitted 0010 trigger syntax error was fixed before the successful
full PostgreSQL run; older committed migrations were never changed.
See `docs/catalog-content.md` for the contract and migration rollback.

Content approval backend/schema are committed in `ec3be26` (migration 0011 is now
immutable). See `docs/content-approvals.md`. Decisions bind the exact saved draft,
catalog/material/brand fields and observed source context; changes invalidate them
without deleting history. Brand changes now invalidate affected reviews atomically.
Approval requires leadership/admin, current context, and acknowledgment plus note
for empty descriptions/tags. No source/external writes or publishing occur.

Verification so far: isolated Docker project
`reawote-test-a16fd027c4424c1ca906796a32684750` passed backend 611, actual PostgreSQL
113 (auth gate 27/27), Linux worker 287, frontend 253, lint/build, no skips. Fresh
and prior-0010 migrations, Alembic current/heads/check, downgrade/re-upgrade,
immutable decisions and competing approval/edit/brand/catalog operations passed.
Own containers/network cleaned and own DB volume retained. Python deprecations and
Pydantic/FastAPI alias warnings remain non-failing. A later explicit active-source
operation assertion passed locally (1 selected, 22 deselected).

The final UI adds a saved-content confirmation, exact retry on unknown outcome,
history and approval refresh even when source changes leave material.updated_at
unchanged. After these UI refinements, all 254 frontend tests, lint/build pass
locally. Final E2E run `d073ae9b-4d7c-4c7e-ab03-2219e6ab9c01` passed all 12 scenarios
on fresh data and all 12 after restart with retained data. It verifies exact saved
content confirmation, credits changing from 12 to 13, invalidation, a new approval,
three immutable content revisions and two distinct approval decisions. Own cleanup
and protected-resource checks passed. The final UI refinements were covered by the
254 local frontend tests and this real Docker-backed E2E; the earlier Docker unit
image contained 253 frontend tests. No skips in either complete suite.

## Completed slice: preview gallery and comparison, 2026-09-17

Worker/API/UI now implement bounded PREVIEW-only browsing and a two-material
comparison. See `docs/preview-gallery.md` and its plan for limits/assumptions.
There is no new migration or source write. Current local targeted backend/access
checks passed 57 tests. Gallery/transport/comparison UI checks passed 34 tests,
lint and production build passed. E2E safety helpers passed all 49 checks.
The earlier worker subset passed 39 actual Linux tests; the later listing-race,
pixel-limit and child-environment additions still await the full run below.

An initial full frontend run passed 286/288; two new comparison tests used an
ambiguous label selector matching both a region and its select. They now select
the combobox role and all 34 new UI tests pass. Two TypeScript test typing errors
were also corrected; neither required weakening a production/security contract.
No full-gallery E2E result is claimed yet.

Full isolated Docker suite `reawote-test-3efd0943f47a4d969c810b89f8774e13` passed:
backend 648, actual PostgreSQL 119 (auth gate 27/27), Linux worker 329 and frontend
288, with no skips. Lint/build and migration checks passed. Own containers/network
were cleaned and its exact named database volume retained. Python deprecations
and FastAPI/Pydantic alias warnings remain non-failing. This includes all six new
PostgreSQL preview access/identity races and all 42 Linux preview tests.
A new real-image E2E scenario is prepared, using only guarded synthetic PNG writes
under the owned run directory. Compare screenshot verification and retained-data
E2E results remain pending. `E2E_KEEP_SUCCESS_ARTIFACTS=1` preserves only the owned
run's synthetic UI screenshots for review; normal resource/data cleanup remains
unchanged. All 49 safety helper checks passed again after adding this option.

First gallery E2E attempt `484d2c98-14f4-4ebe-a6cb-524b9c9d1a0f` passed the existing
12 scenarios but failed comparison when React StrictMode issued duplicate listing
requests and consumed both bounded worker slots. Corrected the shared resource
hook and image effect to skip abandoned work before its first microtask; kept the
worker concurrency bound and HTTP-error gates unchanged. Added a StrictMode
regression assertion. Full frontend rerun and new E2E result remain pending.

All 289 frontend tests then passed, together with lint/build. The next E2E attempt
`51b327df-34d4-448c-aa93-67b41d318782` rendered both real comparison images and its
screenshot was visually inspected, but the unchanged network/body gates detected
an aborted completed preview on selection change. Image cleanup now aborts only
pending fetches and cancels only an unfinished response stream; completed object
URLs are still revoked. That cleanup refinement alone did not resolve the browser
failure: runs `b393d4df`, `ba946c04` and `6eca2a18` still passed 12/13 scenarios.

A standalone loopback Chromium reproduction then isolated the failure from React,
FastAPI and PostgreSQL: direct stream-to-Blob consumption reported ERR_ABORTED
after all image bytes were delivered. The client now bounds a cloned stream before
using the native array-buffer consumer on the original. Cloning performs no extra
HTTP request; oversize cancellation closes both branches. The synthetic browser
reproduction passed all 20 transfers with this implementation. The byte limit,
session behavior, no-store policy and all E2E network/body gates remain enabled.

Final local frontend verification passed all 290 tests, lint and production build.
The final Linux worker suite previously passed 330; after the filename-extension
fix in `952557f`, all 43 actual Linux preview cases passed again. The corresponding
backend preview subset passed 37. No new schema migration was added; 0011 remains
the head. The earlier full Docker run above remains the full backend/PG baseline.

E2E run `5d6a0f7c` passed 13/13 fresh and 13/13 retained-data scenarios. After removing
temporary browser instrumentation and adding a 390px mobile layout check, final
run `b17c058a-8b86-4327-a764-75f26d9e195d` passed all 13 fresh and all 13 after restart.
Desktop and mobile comparison screenshots were visually inspected: both images
retain their proportions, controls remain readable, and there is no horizontal
overflow. Screenshots remain in this run's `.e2e-artifacts` directory. Cleanup and
protected-project/volume checks passed; only owned resources were touched.

## Remaining sequence after current work

Gallery commit `9cb6727` and its ancestors were pushed to the owned remote branch
`codex/autonomous-pbr-completion`. Main was not changed. A draft PR has not yet
been created; the GitHub CLI is unavailable on this host.

Historical source readers now support bounded UTF-8 CSV and explicitly selected
XLSX sheets. All 65 parser/compatibility/security tests pass on Windows Python
3.13.15 and in the owned Linux image `reawote-import-unit-01a0a64d`. No tests were
skipped. The container had no network, ports or host mounts and was removed on
completion. `defusedxml` is a runtime dependency; `openpyxl` is an independent
test writer. See `docs/historical-import-plan.md` for exact limits and conservative
unsupported cases. Source parsing was followed by the administrator import API,
atomic database confirmation, immutable audit migration 0012 and verified UI
described below. Bounded read-only folder discovery is complete as described below;
historical production migration remains unperformed.

1. Historical production workbook/NAS verification when actual inputs are supplied;
   the synthetic CSV/XLSX workflow and explicit one-level folder selection are done.
2. AI draft provenance and generation configuration (`docs/ai-content-plan.md`).
3. Immutable publication jobs, exact nine-field CSV, packaging and historical
   golden comparisons; online importer/production golden assets remain unavailable.
4. Configurable GCS/Notion adapters, contracts and operational instructions.
5. Remaining catalog/account audit coverage, soft-delete/restore, old-history
   pagination, legacy CRUD idempotency and final operations/review documentation.

Live external verification is blocked until separately authorized access and
test targets are supplied. Implementations must never simulate successful live
operations. 3D models and HDRI remain out of scope.

## Resume

Identity worker/API/UI are committed in `4acc874`, `07ef4db` and `0b1e3ff`.
Catalog server/UI and gallery retained-data E2E are verified as noted above.
Historical source inspection and database-backed preview/confirmation are now
implemented, including explicit five-column/reference mapping, permanent number
checks, atomic insertion, actor-scoped idempotency and immutable batch/row history
in new migration 0012. See `docs/historical-import.md` for the complete contract.
The final isolated full Docker project `reawote-test-86961169dfcc424ebcdbca5e3e34bb2a`
passed 818 backend, 127 actual PostgreSQL (auth gate 27/27), 330 Linux worker and
290 existing frontend tests, lint/build, with no skips. An earlier run stopped
at an outdated hardcoded migration-head assertion (812 other tests passed); that
test now checks the complete chain including 0012. SQLite cursor pagination was
also corrected using native stored timestamps and regression-tested.

The import UI now supports explicit file/sheet/column/reference choices, paginated
preview and audit history, actionable bounded errors, and frozen exact retry for
an unknown confirmation outcome. Full frontend verification passed 323 tests;
the later error-code addition passed its 53 focused client/UI tests and lint/build.
Final E2E run `d4fcb3b3-cb1a-4f82-8372-9bba5b72e447` passed 14 fresh and 14 retained
scenarios, including real synthetic CSV/XLSX uploads, counter advancement and audit
history after restart. Desktop/mobile preview and history screenshots were
visually inspected. Own cleanup and protected-resource checks passed. The first
browser attempt exposed a test select-label lookup mismatch; selecting the exact
combobox role fixed it without weakening app validation or browser gates.

Do not claim real historical migration. Administrator-only one-level source folder
discovery is implemented and documented in `docs/folder-discovery.md`. It opens no
files, follows no links, has explicit path/entry/time/concurrency limits, and only
fills the existing form when the folder exactly matches the material identity.
It never links or approves automatically. The backend reauthorizes after worker
I/O. The same correction now protects all three existing preflight/link/Done
routes before source findings or dependency failures are disclosed.

Final isolated Docker project `reawote-test-b9a02bf93fd144d988cf2f698d01b57b` passed
883 backend, 137 actual PostgreSQL (auth gate 27/27), 354 Linux worker and 352
frontend tests, plus lint/build, without skips. The previous attempt passed 854
backend and 127 PostgreSQL tests but six new concurrency expectations incorrectly
expected 403 after an account API role change: that API intentionally revokes
sessions, so the correct response is 401. Tests now assert that contract. Direct
role changes with still-active sessions retain their separate 403 coverage. No
production permission or validation was relaxed. The final frontend image was
refreshed before its checks to include a corrected test-only type import.

Final E2E run `0969494a-23b5-48b7-9fd1-5105fb2ad6f1` passed 14 fresh and 14 retained
scenarios. Source discovery/selection is verified as read-only before ordinary
preflight/link/Done. Desktop and 390px screenshots were visually inspected; no
horizontal document overflow. Owned cleanup and protected-resource checks passed.
Commits `28321bc` (import UI) and `10e1415` (folder discovery/reauthorization) are
pushed to the owned remote branch. An initial push auto-review rejection questioned
the remote's authorization. Read-only checks proved it exactly matched both user
handoffs (iamerikbalaz/Database), and the explicit user push authorization was
provided on retry; the push succeeded without changing the destination or payload.

AI context, administrator source approvals and immutable external proposal intake
are implemented in migration 0013 and API code (backend commit `4d48ff4`). Full isolated
project `reawote-test-0f2a26665ab042599d76456b956b596e` passed 917 backend, 144 actual
PostgreSQL (auth gate 27/27), 354 Linux worker, 352 frontend, lint/build and migration
checks without skips. It covers real source/draft races and immutable provenance.
See `docs/ai-content.md` for exactly what was verified and the remaining scope.

The same backend commit adds explicit human adoption using a shared content
save service. Adoption preserves credits/vocabulary, adds immutable AI ancestry,
invalidates decisions and preserves exact replay. Ordinary save request hashes and
old manual snapshot shapes stay unchanged. These later changes passed 86 focused
backend and 26 focused frontend content tests, lint/build. The new AI transport and
source/proposal/comparison workspace passed 29 additional frontend tests, lint/build
and the E2E TypeScript check. Full project `reawote-test-2a225f8af0d64015b511fd8b232a62af`
passed 927 backend and 145/146 PostgreSQL cases; one new race failed during fixture
setup on a duplicate catalog name. The fixture now uses independently named values;
full rerun `reawote-test-f14624c38ab541a798e729a306003b6f` passed 927 backend, 146 actual
PostgreSQL (27/27 auth gate), 354 Linux worker and 382 frontend tests, lint/build,
without skips. E2E run `f6e6da9c-7ccf-4d95-b18c-e3aa0fd30370` passed the
14 existing cases; the new AI fixture lacked the mandatory online category and was
correctly blocked from approval. The next run `d516139b-c9e8-4b77-9ae2-71984e83cccb`
passed the 14 existing scenarios but its new test selector did not match prefilled
textareas; the rendered fields and accessible names were verified from its DOM and
screenshot. Final E2E run `c2fc1ade-d88c-49cb-a3b9-dd81a4978cd8` passed 15 fresh
scenarios (51.6s) and 15 retained scenarios (34s); desktop/390px screenshots were
visually inspected. Protected resources remained unchanged and owned cleanup passed.
Scoped service credentials (`docs/ai-service-access-plan.md`) and provider calls
are still pending. No live
AI/provider or source URL has been contacted. Continue this work, then the remaining
sequence. Migrations through 0013 are committed and immutable. No migration ran on
production. `origin/main` was rechecked before the backend checkpoint and remains
`88a1f99d748d2a0edbb1fce509e13d18bfc03908`.

AI backend `4d48ff4` and UI `43567b1` are pushed to the owned remote branch. The
next uncommitted slice adds restricted service authentication and migration 0014;
see `docs/ai-service-access.md`. It issues one-time short-lived credentials for one
material, binds access to the issuer's still-valid session/account, permits only
minimal context reads/proposal submission, and supports permanent audited revoke.
Human proposal compatibility and exact replay are preserved. Local focused suite:
93 passed. Full Docker project `reawote-test-ef16ea5d2e5445b3989d3228ff76d91c` passed
955 backend, 155 actual PostgreSQL (auth gate 27/27), 354 Linux worker and 382 frontend
tests, lint/build, without skips. E2E run `04d680f7-7332-4265-81cf-055fad545e69` passed
16 fresh (54.0s) and 16 retained (36.4s) scenarios with real scoped service access and
revocation; protected resources stayed unchanged. A later administrator credential
management UI is being tested separately (initial 16 focused tests passed; one
clipboard-unavailable test was added afterward). Generation adapter remains pending.
Migration 0014 has passed its migration/concurrency checks and is ready to commit;
after committing it must remain immutable. No real provider/source URL was contacted.

Later checkpoint: service backend/migration 0014 are committed as `507beb0`;
migrations through 0014 are immutable. The administrator credential UI is now
verified: all 399 frontend tests, lint/build and E2E TypeScript check passed.
E2E `1e426634-998a-434c-952f-6d0617f063bc` passed 16 fresh (54.6s) and 16 retained
(36.1s) scenarios. Real browser revocation and metadata history survive restart;
desktop and 390px screenshots were inspected without overflow or rendered test
secrets. Owned cleanup and protected-resource checks passed. No skipped tests.
Next is an optional generation adapter with explicit model/key configuration,
offline contract tests and no live paid calls; publication/packaging remain later.

Credential UI is committed/pushed as `ca9455b` together with service backend
`507beb0`. The optional OpenAI CLI now has 64 offline contract/real-application
tests; see `docs/ai-generation-client.md` for explicit commands, minimal disclosure,
one-time credential handling and unchanged-packet retry. It does not read source
pages, auto-adopt content or run from the browser. No paid provider call was made.
Full Docker project `reawote-test-6e02e17510344bbda4003be9220806e0` passed 1020
backend, 155 actual PostgreSQL (auth gate 27/27), 354 Linux worker and 399 frontend
tests plus lint/build, without skips. Fresh/prior upgrade and Alembic checks passed.
Owned containers/network were removed and only that project's DB volume retained.
The latest 16+16 UI E2E above is unchanged by the standalone CLI; the generator
roundtrip uses the real service API with only its provider replaced in memory.
Live provider compatibility/quality and source-grounded generation remain unverified.
Next implementation sequence is in `docs/publication-plan.md`. The pure immutable
CSV serializer (`docs/publication-csv.md`) passed 44 Windows and 44 isolated Linux
tests, without skips. Its Linux run used read-only mounts and no network/database;
the first collection attempt selected the older installed package, corrected by
using `python -m pytest`. It has no HTTP endpoint and performs no real export.
Generator `f4de8a1` and CSV foundation `3e370be` are committed/pushed. A later
uncommitted read-only publication preflight adds `/api/publication-batches/preview`;
see `docs/publication-preflight.md`. It validates the full set of current decisions
and binds normalized metadata to the approved source inventory before declaring
the database inputs ready. It also preserves 512-character technical identities.
The initial focused run had 77 passes and nine failures: eight used a stale
technical-check UUID in test setup; one exposed a missing safe-validation path.
After those corrections, 86 passed. A final expiry regression and two actual
PostgreSQL serialization cases were then added; the local suite passed 87 cases.
Full preflight image `reawote-test-6d6ebf13be7c4ea791789c358034fa7e` passed 1087
backend and 155 existing PostgreSQL tests, but its two new serialization fixtures
failed on a missing `SimpleNamespace` import. That test import is now corrected;
worker/frontend phases were not reached in this failed run. Owned cleanup passed.
The later uncommitted batch API/migration 0015 passed 101 focused local cases
(batch/preflight/CSV/access/head). It now stores exact CSV and approval-bound input
snapshots, actor-scoped replay, permanent audit and protected history/downloads.
Migration 0015 is still under verification and has not been committed. Migrations
through 0014 remain immutable. Six additional actual PostgreSQL batch/concurrency/
migration cases were added; the full final run is in progress. UI, packaging,
execution and upload remain pending; no production migration or publication ran.

Publication preparation now includes a role-gated selection/preview/history UI
and bounded, digest-verified CSV downloads (`docs/publication-batches.md`). The
37 focused frontend cases, build, lint and E2E TypeScript check pass. A real browser
case with a deliberately lost committed response, exact retry, later content edit
and retained historical download is added and awaits its isolated run. Earlier
full projects `reawote-test-dda271f8364c44b9aeef21ccd93d8dd8` and
`reawote-test-fc35cb5a618143d3b2d973b3f8977edd` each passed 1099 backend cases but
failed the metadata migration-chain test: its head, then its complete revision
list still named 0014. Both expectations are now updated and the two local chain
tests pass. The later full project `reawote-test-54b4da6020d0479d9e978e41f040019f`
contains both fixes and all 37 new UI tests; it is running. Previous failed runs
did not reach PostgreSQL/worker/frontend and their own cleanup completed. Migration
0015 remains uncommitted. Packaging/execution/upload and live importer verification
are still pending.

The 54b4da full run passed all 1100 backend tests and 161 PostgreSQL tests
(mandatory auth 27/27). Two new brand-edit races addressed a nonexistent
`/api/published-brands` route instead of the actual `/api/brands` route, so their
expected lock wait was never reached. Both fixture URLs are now corrected; no
locking behavior was weakened. The test image stopped before worker/frontend.
Its owned cleanup passed and a complete rerun was started. The E2E setup likewise
uses the existing `/content/approve` write route. E2E runner safety helpers and
the direct-invocation guard pass; no unguarded fixture or production write occurred.

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
