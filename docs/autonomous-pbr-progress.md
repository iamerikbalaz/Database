# Autonomous PBR completion

## Latest checkpoint (2026-09-18)

Account security backend commit: `8c8eec0c579db99e5aa39c431a2cf6f4be164181`.
Its verified UI/docs follow in the next commit; use `git log -1` for the latest tip.
Previously pushed checkpoint: `352d75f6b76e5e4de1144c9d456c210cd216177c`.
Account security history is implemented and verified: new additive
0022, explicit successful-action/actor metadata without credential values, atomic
bootstrap/admin/host/self-service recording and ADMIN paged API. Initial local
auth/access regression: **138 passed, one fixture setup error, 63.46s**, two warnings.
The error was sandbox access to the shared system pytest temp directory, before
the CLI test ran. Using a fresh GUID directory inside the owned worktree fixed it;
new history + complete CLI + schema/metadata suites passed **71 tests, 39.94s**,
no skips, two dependency warnings. Extended history suite (including three added
host/bootstrap rollback cases): **17 passed, 12.86s**, two dependency warnings.
Actual PostgreSQL verification passed **369 tests, 468.47s**, auth **27/27**, no
skips, four dependency/schema warnings, in owned project
`reawote-test-49ac3b2485714c59adfa755377555a8a`, including 14 new immutability,
invalid provenance, concurrent reset/self-service and prior-schema upgrade cases.
Image ID `9b94640e9d7a1ebaf0341428057507d34115c9b2e92b3ee4727e52152ef2757f`.
Owned test containers/network were removed; own volume/image retained.
UI is implemented: **41 focused tests passed, 2.77s**, lint/build passed.
Its initial component assertion matched both expanded and collapsed events (40
passed, one failed); the selector is now scoped to the explicitly opened event.
Full frontend: **798 passed, 27.98s**, E2E TypeScript passed. Browser assertions
cover admin provision/reset and self-service history, including retained reads;
the capability runner `df13cefd-7b5b-439c-a319-b187dae1c80d` passed **19 fresh +
19 retained scenarios**, 1.5m/50.8s. Actual account provisioning/reset and self-service
events remain readable after restart, including disabled profiles. Desktop and
390px security-history screenshots were visually inspected without overflow.
Owned cleanup and protected-state checks passed; owned volumes/artifacts remain. See
`docs/account-security-history.md` for the contract and verification boundaries.
Full Linux regression passed **1875 tests, 1144.76s**, no skips, two dependency
warnings, in owned image
`reawote-account-security-backend-47bb3883962e42e1b8d182214f1ec687:test`, ID
`28e5c4dabac37288479e5ef5964666ab06b6792ab6be25fc233ed408c10c4a9e`,
nonroot/read-only/no network/host mounts. Its owned container was automatically
removed. Migrations through committed 0021 are unchanged. The new ledger is ready
for review; it has not been deployed or run against a production database.
An independent synthetic probe also confirmed bound parameters appear in default
database exceptions. The next small hardening step is recorded in
`docs/database-error-redaction-plan.md`; no real secret or database was involved.

Resource history backend is committed as `584ec48c43ee1d7813dc6c099cc7d0de256c33e8`.
Its UI and documentation are fully verified below; use `git log -1` for the latest
branch tip rather than a historical checkpoint's commit.
Ordinary brand/project/user/material history is committed in the backend: additive
0021, immutable snapshots, atomic create/PATCH audit, and ADMIN paged reads.
Local affected resource/access tests: **66 passed, 40.02s**; history/schema/metadata:
**86 passed, 53.38s**, two dependency warnings each, no skips. Actual PostgreSQL
verification passed **355 tests, 427.57s**, auth **27/27**, no skips, six dependency/
schema warnings, in owned project `reawote-test-9f23ed7c16f74c2bbba892718ea99331`.
Image ID `ae678e3095ab7ba6b60e50f32bb06e77810e5e3a87244298ab59045e49a98738`.
Owned containers were confirmed removed; own volume/image retained. Shared historical
downgrade fixtures now use their own isolated database where later audit evidence
would otherwise mask the older guard they intend to test. UI is implemented;
**101 focused tests passed, 2.97s**, lint/build and E2E TypeScript passed. Full
frontend: **761 passed, 22.77s**. Full Linux backend: **1858 passed, 1129.48s**,
no skips, two dependency warnings. Linux image
`reawote-resource-audit-backend-836e92fb37794d3b99ad32890915bc2b:test`, ID
`dcd848ec5a92ceac01db534e4fa809c9cc726ac8dfbe872b6ec69086a0c69a13`, nonroot,
read-only, no network/host mounts. Real fresh/retained browser verification for all
four resource histories ran as `017b598a-8e99-4615-99b9-0b0d2136125b`:
**18 passed, one failed, 1.5m** on the fresh pass; retained pass was not run.
The new long synthetic brand identifier exposed real mobile overflow in the
existing detail facts/name layout. Its screenshot/DOM were inspected. Detail grid
values and headings now wrap long words within their available width; the test
keeps the long values and full no-overflow assertion. Full browser rerun
`d8705d8f-8f54-414f-a535-7e1ed24e9fc1`: **19 fresh + 19 retained passed**,
1.4m/48.8s. CSS build and E2E TypeScript checks also passed. All four mobile history
screenshots and the desktop project history were visually inspected: legible values,
clear before/after layout, no horizontal overflow. Own cleanup and protected-state
checks passed; owned volumes and synthetic artifacts retained. The Linux container
also completed and was automatically removed; its immutable image remains.
Owned cleanup/protected-resource checks passed; diagnostics were retained.
The capability runner's protected-state snapshot was read and confirmed to cover
only original/demo/default-E2E resources, excluding the independent no-network Linux
regression container. Neither runner targets the other's resources.
See `docs/resource-history.md`.
The next small security-history gap is mapped in `docs/account-security-history-plan.md`.
Migrations through 0021 are now immutable. Resource history is ready for review;
account security history is verified above; material archive remains unimplemented.

Company history is committed/pushed as `27b1e468ac8d846e944a259ff8480a411fb1944b`.
Migrations through 0020 are now immutable. Reviewed local Notion adoption,
actor/key-bound exact replay and read-only recovery are committed as `c84c85a`.
The field selection/reason/confirmation UI is implemented and verified, including
exact retry and read-only recovery. Main was reverified unchanged at `88a1f99`.
No real Notion write/read or production
migration occurred. Details and boundaries: `docs/notion-adoption.md`.

Adoption verification: initial **104 affected API/preview/history tests passed,
100.91s**; extended adoption/access checks **73 passed, 83.43s** (two dependency
warnings each). Actual PostgreSQL: **314 passed, 376.19s**, auth **27/27**, no skips,
four dependency/schema warnings. Project `reawote-test-aed8b4c8babb44229c73977fdc049e6a`,
manifest `748de992f8cdf0042aca729a902f4b1117d1c0c24f6c989a1c5f922870cd963b`.
Owned containers/network removed; own volume/image retained. Includes ten new
concurrent adoption cases against independent readers and real application APIs.
Full Linux backend: **1823 passed, 1035.38s**, no skips, two dependency warnings,
in nonroot/no-network immutable image
`reawote-notion-adoption-backend-0b80e39dd1c54080b3a1607bf96f7be0:test`, image ID
`7d281e202877b296e7f349dd321819bf028dd3780fa2665295385b1ce1446a2f`.
Its owned container was automatically removed; its image remains.

Frontend: **68 focused tests passed**, lint/build passed after correcting a default
UUID parameter's inferred TypeScript type. Full frontend: **662 passed, 21.56s**,
lint/build passed. The later E2E typecheck exposed missing Vite ambient types while
checking the newly shared adoption request type; its configuration now includes
those browser types. A subsequent UI-only check also validates every mapped
pre-change value in adoption responses. All **53 affected client/component tests
passed**, 3.41s, including two new regressions; lint/build and E2E TypeScript passed.
E2E `c25476f1-2c0c-4f25-95af-c18d9658e53c`: **18 fresh + 18 retained passed**,
1.3m/44.7s. This verifies actual disabled adoption rejection, real persisted company
history, and explicitly intercepted synthetic UI adoption/recovery with an unchanged
real company. Desktop and 390px adoption screenshots were visually inspected with
no overflow. Own cleanup/protected-resource checks passed; own volumes and synthetic
artifacts retained. Keep browser synthetic success separate from real enabled-Notion
integration verification, which remains unperformed.
The next bounded sequence is in `docs/resource-audit-plan.md`: ordinary resource
history, then explicit reversible material archive with no source/cloud deletion.

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
  explicit start/recovery/abandon controls and UI.
- Disabled-by-default Notion comparison and reviewed selective local adoption,
  request-bound recovery, immutable company history and UI.
- Ordinary brand/project/user/material audit history and successful account security
  history, with immutable PostgreSQL ledgers, atomic writes and ADMIN paged UI.

These are bounded implementations, not a claim of production-ready PBR completion.
Detailed contracts live in the linked README feature guides. Previous test results,
resolved failures, earlier decisions and environment checks are preserved without
loss in [historical checkpoints](autonomous-pbr-history.md).

## Remaining work and real blockers

1. Close the confirmed database exception-output gap:
   [bounded hardening plan](database-error-redaction-plan.md).
2. Implement conservative material archive/restore with full authorization and
   asynchronous-operation coverage: [plan and integration map](resource-audit-plan.md).
3. Complete remaining history pagination/legacy CRUD replay decisions, artifact
   cleanup lifecycle and operational/final review documentation.
4. Verify the actual online importer contract, golden material outputs, manual
   publication confirmation and realistic historical workbook/source compatibility.
   Production inputs and importer fixtures are not available in this environment.
5. Finish external credential lifecycle/configuration where necessary and verify
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
All committed migrations through 0022 are immutable; append new migrations.

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
