# Autonomous PBR completion

## Latest checkpoint (2026-09-18, material lifecycle verification)

Latest pushed tip: `221c9c0b895fe0549dd7cc3670c4c5834d32ba69`. Archive backend/UI,
account security history and the database exception boundary are committed and pushed. Remote main
was last verified unchanged at `88a1f99d748d2a0edbb1fce509e13d18bfc03908`.
Earlier test runs and resolved failures are preserved in the historical checkpoints.

Material lifecycle backend is committed as `e9964c5136a26c39e5d18f5284a25764951d3711`.
Its UI/docs are committed as `221c9c0`; use `git log -1` for the current branch tip.
Lifecycle 0023 is implemented and wired: ADMIN preview,
archive/restore, exact command recovery, bounded listing/history, current-work
denial and narrow historical packaging/download reads. Its UI supports explicit
confirmation and uncertain-result recovery; restored identity/number/assignment
are preserved while metadata/review readiness is reset. Any prior storage dispatch
blocks archive, including abandoned jobs; an unsent closed reservation is allowed.
See `docs/material-archive-contract.md` for behavior and rollback restrictions.

Verification collected so far:

- Initial local archive tests: 26 passed, 28.31s; schema/metadata: 51 passed, 27.53s.
  Expanded access/history/source-IO regression: 108 passed, 126.47s.
- Initial PostgreSQL lifecycle suite: 386 passed, 467.74s, auth 27/27, no skips,
  six dependency/schema warnings. Owned project
  `reawote-test-bcf6fda830a9412eae5fad5147a2e516`, image manifest
  `e3c8bb65ac7a8d78b73c275aaa8911f000e4017d3e06fcd9e3e9cb527cda462d`.
  Containers/network removed; own volume/image retained. This predates final router
  wiring, packaging-history exception and the prior-storage-dispatch guard.
- Updated local archive suite: 33 passed, 55.22s; extra 22-record pagination case:
  1 passed, 33 deselected, 6.03s. Each had two dependency warnings.
- Frontend: 844 passed, 23.65s. After archived packaging/storage-control tests:
  50 focused passed, 4.53s; lint/build and E2E TypeScript passed. The later
  per-actor pending-packet retention correction passed 14 focused tests, 3.11s,
  followed by lint/build (2.26s).
- Actual browser run `74fff25f-c95d-414e-8855-0da48ed9c730`: 20 fresh + 20 retained
  passed; retained 54.8s. Includes committed archive with lost response, read-only
  recovery, restore, exact retry after a second lost response, preserved identity
  and restart history. Desktop and 390px screenshots visually inspected without
  overflow. Owned cleanup/protected-resource checks passed. Synthetic artifacts and
  owned volumes retained. This predates the explanatory external-state message and
  the later per-actor pending-packet correction.
- Final browser attempt `90272184-51cf-4bc0-806e-9ff73f6d8255`: 19 passed, one
  failed, 1.5m; retained pass was not run. The unrelated metadata-dimensions
  scenario observed Chromium `ERR_NO_BUFFER_SPACE` on GET `/api/brands`. Archive
  itself passed. Cleanup removed own containers/networks; diagnostics and own
  volumes retained. A later read-only host socket snapshot showed 747 TIME_WAIT,
  30 established connections, against a 16384-port dynamic range; it does not prove
  the root cause at failure time. The assertion was kept unchanged. Full rerun
  `17161905-c2b2-461a-8b3c-2ddf7ce8ec21` passed **20 fresh + 20 retained**,
  1.4m/52.7s. New mobile screenshot visually inspected without overflow; desktop
  layout was already inspected on the earlier successful run. Owned cleanup and
  protected-resource checks passed; own volumes and artifacts retained.
- Full Linux backend: **1920 passed, 1215.16s**, no skips, two dependency warnings,
  image `reawote-material-lifecycle-backend-055edda3b47446acbf9f3238484b0de8:test`, ID
  `92a24e6a13829b2dd712cd29f579b0f1630234fc24ab6e4b58025eb2540cc771`.
  Nonroot/read-only/no network/host mounts; predates prior-storage-dispatch guard.
  Owned container automatically removed.
- Updated PostgreSQL: **389 passed, 491.37s**, auth 27/27, no skips, four warnings.
  Owned project `reawote-test-f0e7c9d741fe4024aaa31b11a89de730`, manifest
  `1ec4dbece0f11cef80942c5187c3a84ca4a61eb7e9e2aec6eb10034808efe6a4`.
  Includes prior dispatch rejection in API and PostgreSQL, including an API-bypass
  test. Owned containers/network removed; own volume/image retained.

- Expanded PostgreSQL run: **391 passed, one failed, 517.68s**, auth 27/27,
  no skips, five warnings. Owned project
  `reawote-test-39a364bbc8af49b49c673eda18640f7f`, manifest
  `bd88de38410dbdff5606e6b237740e4ad89bcda7939a56bacaaef7c35264a2d1`. Covers both orderings of
  archive vs packaging reservation and account revocation during archive commit.
  The new archive-first test passed its HTTP/race assertions, then incorrectly
  counted packaging records for other fixtures in the shared test database. Its
  final ownership assertion now scopes to the tested material.
  Owned containers/network removed; own volume/image retained.
- Corrected focused PostgreSQL run: **49 passed, 55.35s**, auth 27/27 plus all 22
  lifecycle cases, no skips, two warnings. Owned project
  `reawote-test-bfa6ad1f1f7b4d4d990a48bef92afffe`, image manifest
  `3501f02a6417303665615ed38c7b9361a0fe4a05b59d9c8f6bc5b5ef24dd3282`.
  A temporary copy of the standard runner selected auth+lifecycle only, preserving
  all isolation checks and the mandatory auth gate; the copy was removed afterward.
  Owned containers/network removed; own volume/image retained. This image also
  contains initial, uncommitted ADC work, disabled in these tests.

Request-level GCS credential renewal and an opt-in ADC provider are implemented,
wired and verified, ready for their own commit. Final local verification passed
**136 tests, 3.80s**, including the installed Google Auth 2.58.0 SDK with a synthetic
ADC file, service-account signing and Google metadata responses. Affected transport/batch/runtime regression
also passed **179 tests, 114.96s**, two warnings, before the added ADC cases.
Linux verification passed **280 tests, 275.89s**, no skips, two dependency warnings,
in owned image `reawote-gcs-credentials-1726fce9943d41959977d081016a3b2f:test`, ID
`d57af828176a7969eb45ff09af6957bf412954e9b6393d217dc68048b795e9c9`.
Nonroot/read-only/no network or host mounts; owned container automatically removed.
The two final service-account SDK cases were added after the Linux snapshot and
passed locally. No real credential/account was used.
Initial transport
tests passed 110 with one new expected-request-count assertion failure: the final
PUT already returns metadata, so the actual protocol correctly has two later GETs,
not three. The corrected assertion preserves exact write counts. See
`gcs-credentials.md`. No schema or frontend change is required for credential renewal.

Committed migrations through 0023 are immutable; append a new migration for later changes.
No original/demo/restore database, NAS or real external target has been modified.

## Scope and implementation status

Worktree: `C:\Database\Database\tmp\autonomous-pbr-completion`.
Branch: `codex/autonomous-pbr-completion`, based on verified `origin/main`
`88a1f99d748d2a0edbb1fce509e13d18bfc03908`. The user's adopted autonomous-development
request authorizes this isolated branch, additive migrations, synthetic tests and
pushes. Main, deleted `feature/auth-ui`, original work, NAS, backup/restore resources
and production databases are excluded. No merge/deploy or real external write.

Implemented and tested within the documented contracts:

- Local Docker isolation, endpoint gates and initial material consistency fixes.
- Session authentication, CSRF, current-role/material authorization, forced password
  change, account administration/recovery and new authentication UI.
- Source inventory, technical reports, technical/content approval, audited reopening
  and invalidation, controlled identity changes and filesystem recovery journals.
- Catalog/content editing, historical CSV/XLSX import, bounded source discovery,
  preview gallery/comparison, AI proposal provenance/adoption and scoped services.
- Publication preflight, immutable export batches and exact CSV; packaging planning,
  conversion/ZIP execution, durable reservations/dispatch/lease/recovery, accepted
  proof and authorized historical downloads.
- Configurable GCS staging transport, bounded source streams, immutable staging jobs,
  explicit start/recovery/abandon controls and UI, plus opt-in renewable ADC credentials.
- Disabled-by-default Notion comparison and reviewed selective local adoption,
  request-bound recovery, immutable company history and UI.
- Ordinary brand/project/user/material audit history and successful account security
  history, with immutable PostgreSQL ledgers, atomic writes and ADMIN paged UI.
- ADMIN material archive/restore with preserved identities/evidence, current-work
  exclusion, exact recovery and explicit protection against unresolved external state.

These are bounded implementations, not a claim of production-ready PBR completion.
Detailed contracts live in the linked README feature guides. Previous test results,
resolved failures, earlier decisions and environment checks are preserved without
loss in [historical checkpoints](autonomous-pbr-history.md).

## Remaining work and real blockers

1. Complete remaining history pagination/legacy CRUD replay decisions, artifact
   cleanup lifecycle and operational/final review documentation.
2. Verify the actual online importer contract, golden material outputs, manual
   publication confirmation and realistic historical workbook/source compatibility.
   Production inputs and importer fixtures are not available in this environment.
3. Finish external credential lifecycle/configuration where necessary and verify
   isolated live GCS/Notion/AI integration only after separately authorized targets
   and access are supplied. Contract tests do not substitute for that live check.

3D models/HDRI remain later scope. Backups are unchanged; off-machine backup custody
is still unconfirmed. No production migration, publication or source deletion has
been performed. A draft PR has not been created: no available GitHub CLI/PR connector
was found. The owned remote branch is the review artifact in the meantime.

## Resume and verify safely

Read the latest checkpoint first, inspect this worktree's branch/HEAD/status and
compare remote main with the known base. Preserve the user's original checkout and
all unrelated work. Never use the deleted experimental authentication branch.
All committed migrations through 0023 are immutable; append new migrations.

- PostgreSQL: `./scripts/test.ps1 -PostgresqlOnly` from this worktree with the local
  Docker CLI on PATH. It creates a fresh owned namespace and mandatory auth gate;
  never substitute the demo/original databases. Its output redacts ephemeral DB
  credentials. It removes its containers/network and retains its own volume/image.
- Linux backend: use the recorded owned immutable image, nonroot, read-only,
  no network/host mounts; include Linux-only worker-boundary tests. Collect its
  terminal result and preserve its exact image identity.
- Frontend, from `frontend`: `npm test`, `npm run lint`, `npm run build`, and local
  `node_modules/.bin/tsc --project tsconfig.e2e.json --noEmit`.
- Browser: `./scripts/test-demo-e2e.ps1` with
  `E2E_PROJECT_NAME=reawote-e2e-auto-01a0a64d` and
  `E2E_KEEP_SUCCESS_ARTIFACTS=1`. The capability runner owns its synthetic paths,
  loopback ports and volume; direct Playwright invocation is forbidden. Finish
  changes to protected Docker resources before its snapshot. An independent,
  no-network/no-mount regression container does not enter that snapshot. Require both fresh
  and retained passes and inspect new desktop/mobile artifacts.

Commit only explicit owned files after checks, then push only
`HEAD:refs/heads/codex/autonomous-pbr-completion` to the verified repository remote.
Keep test evidence and unverified boundaries accurate. Continue the next unblocked
slice; do not treat one completed commit as completion of the autonomous task.
