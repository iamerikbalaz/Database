# Autonomous PBR completion

## Latest checkpoint (2026-09-25, historical R: sample)

Resumed from `44fcb2673fd606290ec660536e06006c894bb2a1`; freshly read remote main
still equals `88a1f99d748d2a0edbb1fce509e13d18bfc03908`. Original checkout remains
clean. The user's latest direction narrows source operations: catalog references
must preserve the existing tree, historical materials have no project, and any
future material rename/new directory workflow must follow the manufacturer rules.
See [the actual 100-row acceptance and remaining limits](historical-r100-acceptance.md).

Implemented:

- Four-part named identities across new record creation, historical import,
  technical image proofs, packaging map names and controlled identity planning.
  Exact historical spelling is retained; persisted three-part records remain
  readable. Missing metadata remains nonblocking for catalog import.
- Optional historical project and explicit relative folder references. No source
  IO occurs during import. Catalog overlaps and active source ownership block a
  batch; confirmation reacquires ownership before writing. New migration 0026
  makes project nullable and refuses downgrade with unassigned records. Migrations
  0001–0025 are unchanged.
- Nullable project rendering, later assignment, optional import mappings and
  folder paths directly in the material list. Imports page is loaded separately.
- A private 100-row workbook and actual isolated API/browser acceptance. The app
  and its PostgreSQL remain available locally for manual testing; source writes,
  packaging and external integrations are disabled. No agent runs in the service.

Verification for this increment:

- Relevant backend/import/naming suite: **411 passed**, 170.28s. Final identity
  protocol regression after the E2E-discovered base-name mismatch: **39 passed**,
  61.00s. Late folder collision/active-owner tests: **4 passed**, 10.31s.
- Full frontend: **951 passed**, 39.02s; lint/build, E2E TypeScript and direct
  invocation guard passed. Main chunk 474.01 kB; no raised warning threshold.
- Actual PostgreSQL: **464 passed**, 838.31s, auth **27/27**, no skips. Project
  `reawote-test-45e8196aab7d4c3ab058c0cc1080f1ed`; owned containers/network cleaned,
  database volume retained. An earlier run had 463 passed and one obsolete
  three-part 9999 assertion, corrected before the full green rerun.
- Full Linux worker/packaging image: **822 passed**, 586.23s, no skips. Image `.Id`
  `sha256:460cc39264c882882d37f59023afc986d297b3b998791e06c1a11d290375a17b`.
  The earlier thin-image run's 330 skipped packaging tests are not claimed as
  acceptance; the full dependency image supplied the final result.
- Guarded browser run `56a28942-2b11-4d69-8664-a94686fb8efd`: **24 fresh + 24
  retained passed**, 2.2m/1.3m. Protected regular/demo resources unchanged; owned
  containers/networks removed and volumes retained. The first browser run caught
  backend proof validation missing the worker's base-name rename support. Fixed
  strictly, with forged-prefix negative tests, then reran the complete browser suite.
- Actual private R: subset: **100 records/100 exact paths/100 NULL projects**,
  identical import replay, all API details verified after PostgreSQL restart and
  actual UI search/detail/reload/mobile checks. No real NAS gallery/texture test
  is claimed because Docker cannot currently mount that network drive.

Inspected final E2E image `.Id` values:

- backend `sha256:c48786de9a9b4c285e5127b3428d6934fbe39b42cea25151fd13cee32020e4c2`;
- frontend `sha256:f4d3cb15b8d3afd36f0ea73fff5de2d7231cdb8e82f9d4d125d2612b234c96b3`;
- worker `sha256:d09e268786889c69dc2572e538f04eea3a80bdefd319f42f6c0ce755836e1674`;
- packaging `sha256:fd5ffa4da6824ffb70dad4e6b38b3d3dc02b6dc85080ac22aa89c14b90eda1e2`.

Implementation checkpoint: `e590ada`. Private source values and credentials are excluded from git. No main merge,
deployment, original database migration, NAS write or real cloud operation occurred.

## Previous checkpoint (2026-09-25, production dashboard)

Resumed the owned `codex/autonomous-pbr-completion` worktree from clean
`8d86c72dc3beb12c472416a3746c4694b5bf2b9a`. Remote `origin/main` was read again and
remains `88a1f99d748d2a0edbb1fce509e13d18bfc03908`. The original checkout is clean.
The removed experimental auth branch was not inspected or used.

The remaining home-page placeholder is now a [production dashboard](production-dashboard.md):
one authorized material-list snapshot, recorded status counts, filters/search,
ten results per page, manual refresh and ordinary workflow links. Processor scope
comes from the existing server endpoint. Account/role changes discard prior data
and late responses; failed refreshes hide stale counts. DONE is explicitly separate
from approval/publication. No new backend API, dependency, migration or external IO
was introduced. Existing migrations 0001–0025 are unchanged. Browser pagination
does not solve the existing full-list endpoint's unverified production scaling.

Verification for this increment:

- Before edits: **50 relevant existing frontend tests passed**, 8.37s.
- Initial focused Dashboard/App/regression run: **36 passed**, 5.26s.
- Initial lint/build found an unused predicate parameter and a possibly undefined
  list in a callback; both were corrected without weakening checks.
- Full frontend suite: **949 passed**, 33.37s, no skips. The initial build then
  warned about a 502.68 kB main bundle; the dashboard is now loaded separately.
- Final affected Dashboard/App/auth/regression suite: **53 passed**, 5.30s.
  Lint, production build and E2E TypeScript check passed. Main chunk is 498.56 kB,
  dashboard chunk 4.46 kB; no bundle-size warning or raised threshold.
- Real browser run `ae824fd0-546e-405c-aa80-75b66baec976`: **24 fresh passed**, 1.8m,
  and **24 retained passed**, 1.0m, no skips. Dashboard assertions compare real
  API records, processor assignments and counts, navigate to actual details,
  and recover an intentionally aborted read without any dashboard write.
- Four dashboard screenshots (fresh/retained, desktop/390px) were inspected;
  no horizontal overflow. Artifacts remain under the owned run's
  `.e2e-artifacts/.../playwright-results/{fresh,retained}` directories.
- Direct Playwright invocation safety check passed with zero POSTs/fixture writes.
  The runner preserved protected regular/demo state and removed only its own
  containers/network/run fixtures; owned database/identity/packaging volumes remain.

Inspected E2E image `.Id` values, in component order:

- backend `sha256:61d158e7a15efb11a03c8a4d0a48247476507c44ac1caaba065df6b73d53a411`;
- frontend `sha256:9e4811c2adfa2a60e38e9a8a26db84985ce9e60195fb6b019e6a702e78093b6a`;
- worker `sha256:f9ed13388c0d2b58dc727a46cfb1e2d623a118fab2ef13ca4422b720ba048ee9`;
- packaging `sha256:e97940d101ef8dde218c5e9e796c38302f46ac5dce0f61e9b3201d4935670ec7`.

The standalone PostgreSQL/concurrency and complete Linux worker suites below were
not rerun for this frontend-only change; their dates and scopes remain explicit.
The Docker browser run used its actual newly built backend/worker/packaging images.
No test process remains running. No merge, production enablement or external write
occurred. GitHub CLI and a direct GitHub connector remain unavailable; no draft PR
was created. The remote branch and review handoff remain the review artifacts.

The actual importer/golden PBR reference, representative historical inputs, live
integration targets and deployment/backup decisions remain missing. The next
acceptance task is still one real reference CSV/manifest/ZIP compared with the
intended importer. Dashboard work does not remove these blockers.

## Previous checkpoint (2026-09-19, verified PBR review candidate)

Owned worktree: `C:\Database\Database\tmp\autonomous-pbr-completion`.
Branch: `codex/autonomous-pbr-completion`. Implementation checkpoints:
`1ed3b44339b42558513ebdf19ddb32b8e52545e1` (authorized recoverable retirement API)
and `776a36a4a8d3aef4f7f4a8a8eb4464bfd657972e` (reviewed UI and actual E2E).
API/migration 0024: `249b9e6e3a88495544b8eaaaf44c5ba50241972c`; Docker source
mapping guard: `c3a0d1631f06e372284409da5fe003b472cd0b45`. Remote main last
verified unchanged at `88a1f99d748d2a0edbb1fce509e13d18bfc03908` during that push.
Shared controller extraction is committed as `9957a0ef8729f23175412ac2f295886c35d76bc2`.
Account profiles (`6c83a33`), temporary error cleanup (`7565a1c`), incomplete retained
copy cleanup (`e5679b6`) and reconciled docs have been pushed. Remote main remains
unchanged at the stated base.

The [consolidated review handoff](pbr-review-candidate.md) records implemented
behavior, safe reproduction, migration/rollback boundaries and concrete missing
external inputs. All six baseline migrations compare unchanged against main.
There are no running test sessions at this checkpoint. No merge, production
enablement or external write was performed. Remaining acceptance work below needs
real fixtures/targets or a separately authorized deployment/integration step.

### Application retirement API and operator controls

The [application API](packaging-retirement-api.md) implements ADMIN/CSRF/proof-bound
intent and recovery with dedicated execution leases and short transactions.
Committed intent quarantines new downloads/staging; existing worker-locked
readers retain account rechecks. Exact replay performs no IO. Factual receipts
survive actor revocation or lease loss, and another administrator can recover.
The separate backend flag defaults false. No production enablement occurred.

Relevant expanded API verification passed **117 tests with three test-adapter
failures**, 299.85s. All 24 retirement cases passed; the existing download failure
spy needed to forward the new `opening` keyword. After correcting that adapter,
all **32 targeted download/config cases passed**, 10.79s. The first focused run
was **20 passed / 1 failed**, because the disabled-feature fixture tried to mutate
frozen Settings; it now constructs the app with a copied Settings value.
The final focused retirement/download suite passed **48 tests**, no skips, two
dependency warnings, including an already opened reader finishing while new
readers are quarantined and the synthetic worker reports BUSY to removal.

The first complete application PostgreSQL run was **462 passed / 1 failed**,
674.89s, **auth 27/27**, no skips, four dependency warnings. Run
`reawote-test-95e57e08988a40949299c5d2b7d5d448`, image
`sha256:470a09f99860da0b5f2fd53abee158a336860138921a5c22d4e5884c96e5eeb8`.
The new real account-demotion test expected 403, but the established account
endpoint revokes sessions on role changes and correctly returns 401. Only that
expectation/comment changed; guards remain intact. Full corrected PG run
`reawote-test-e58187a18ac2461bbe69ff3d8c40e977` passed **463 tests**, 661.61s,
**auth 27/27**, no skips, five dependency warnings. Owned containers/network were
removed; test image/volume retained. Inspected image:
`sha256:1674244796e07dd1a7e5c1c125182daaf6426f4f0fc5745813fc2eb06e5619cf`.
Runner offline gates: **10 passed**, no skips.

UI/client work is implemented: read availability before download controls,
explicit ADMIN confirmation, exact unknown-response packet, read-only recovery,
actor/proof-bound late-callback isolation, monotonic REMOVED display and history.
The frontend suite passed **935 tests**, 27.19s; lint and E2E TypeScript passed.
After actor/time display, wrapping and lazy loading the copy controls, all **64
affected client/component tests passed**, 3.72s; lint/E2E TypeScript passed again.
The final build passed in 2.79s. Loading the copy controls only when needed moved
11.36 kB into its own chunk and reduced the main bundle to 498.37 kB, eliminating
the earlier 508 kB bundle warning without raising the warning threshold.
The first browser run `f32051b5-4e0a-4eae-aa09-afa36b7c75f3` passed **22/23 fresh
scenarios**; the new scenario searched compact packaging summaries for a reason
that is only in job detail. The test now reads the real details before selecting
its two accepted copies; application contracts remain unchanged. Retained tests
did not run after that failure. Final run `d2817df1-6b1b-4262-a6b2-9eb6f9c682af`
passed **23 fresh scenarios**, 1.7m, and **23 retained scenarios**, 58.6s, no skips.
This uses the actual browser/backend/PostgreSQL/private packaging service. It
creates two real accepted copies, rejects removal while staging owns one, retains
the original download across restart, removes only the second copy, deliberately
loses its committed response and recovers by GET without another POST. Removed
downloads remain blocked after restart; PACKAGED proof, CSV and closed staging
history remain unchanged. Four fresh and two retained desktop/mobile screenshots
were visually inspected; 390px pages have no horizontal overflow.

Inspected E2E images:

- backend `sha256:04bd625f6776db51249fc095e88af20a59350e255da079753586267477023731`;
- frontend `sha256:79f5e1e5afacb1a51c3075b2c2b9569eec13083403df6c5e943789453afde6ee`;
- worker `sha256:00bf377a370e03245e0d230d013e91feb7ab5ebca6d9f10f02f56cb4ab3fd112`;
- packaging `sha256:1d191b5d32de0472656d37ec0e468ce49b5187de83123392b0dc0bdd1d6cf11b`.

The runner removed only its containers/network/run fixtures, retained its database
and identity/packaging volumes, and confirmed protected regular/demo state unchanged.
All E2E helper safety cases passed; direct invocation failed closed with zero POSTs/fixture writes.
The browser's fresh/retained screenshot directories are now separate, preserving
review evidence from both passes. No real cloud access is enabled.

### Retirement database evidence (0025)

The additive [retirement schema](packaging-retirement-database.md) now preserves
intent, ordered dispatch and factual observation as append-only evidence, while
keeping existing PACKAGED history unchanged. Matching references, accepted-proof/
manifest checks and material-row locking exclude active/new staging claims for the
same copy. Verified receipts bind every intent field; late uncertain facts cannot
erase a receipt. Populated retirement evidence refuses downgrade.

The complete isolated PostgreSQL phase **passed 459 tests**, 641.03s, **auth 27/27**,
no skips, four existing dependency warnings. Run
`reawote-test-2e40bc8235254f23a219e729ecd0d40b`, immutable backend image
`sha256:fa541a68885aefb1eaead568a90344f544b35a2dc1775f6563a189d8b0f4438e`.
This includes fresh/prior upgrade, Alembic current/heads/check, strict receipt and
history guards, empty/populated downgrade and real competing retirement/staging
claims. Owned containers/network were removed; owned test image/volume retained.
Relevant local schema/packaging backend tests **134 passed**, 213.74s, no skips;
the runner's **10 offline isolation cases passed**.

That schema checkpoint and immutable image predate the separately verified
application route/config/download integration described above.

### Private retirement boundary

The separately opt-in private retirement endpoint holds the recorded execution
lease and binds its READY proof/roots before removal. It preserves execution
history and supports exact receipt recovery with NAS offline. Delayed ordinary
dispatches inspect retirement state before writing any command, so a late CLOSE
cannot strand receipt recovery in CLOSING. New downloads report a fixed retired
code. No application user action, database migration or UI is enabled yet.

The independent backend client validates every receipt binding and file/byte
total against the accepted request/report/result; responses are at most 4096 bytes
and 150 seconds, without automatic retry. Final relevant backend client tests:
**164 passed**, 3.60s, no skips, two existing dependency deprecations. They include
independent validation of an actual HTTP/restart/lost-receipt synthetic export.

Before the additional delayed-dispatch fence, the affected Linux suites passed
**201 tests**, 280.10s, no skips, two existing warnings. Owned run
`reawote-retirement-api-947f1213c9f54d89834371d5d7c97919`, image
`sha256:e402ae3ec89cbb2a4ce3f8573efcdd4640185c696e2606437404410cefd7c8b7`.
Actual HTTP/conversion/closure/offline retirement/lost-receipt/restart smoke and
synthetic contract export passed in owned
`reawote-retirement-smoke-c6ef9d25a3af49faa4aa1d3e259bd154:runtime`, image
`sha256:0ab0c5c2a0db237cc00af29b411aec3bad2d62c5d53b7a09eeae67a3d626cdb3`.
Final affected Linux suites, including the dispatch fence: **209 passed**, 295.96s,
no skips, two existing warnings. Run
`reawote-retirement-fence-7e312e1d687b4db98deafc8459948b54`, image
`sha256:10503f303f88fe0806b797c98f0a5d2b6decf386530cf956770e09934f95302d`.
The final production HTTP/conversion/download/offline/closure/retirement smoke also
passed, including a new delayed reconciliation that leaves execution history
unchanged. Run `reawote-packaging-service-cf0be92a7fef4fd3b210fc6ca321e863:runtime`,
image `sha256:e184c1f145361c655e89318690a4d52d0968e87b7da8468136d51a359d153fab`.
All owned test containers were removed and images retained. This checkpoint
accompanies the private-boundary commit; read the branch tip for its exact hash.
The private-boundary image predates the separately verified 0025 schema above.

### Accepted-copy storage retirement

The internal [retirement primitive](packaging-retirement.md) now verifies an exact
READY result and persists a version-2 REMOVING intent before byte removal. It keeps
the entire original proof/attempt record, binds root/operation and file identities,
supports exact interrupted replay and leaves a permanent REMOVED receipt. Legacy
storage reads/recovery/retention reject both retirement states. No HTTP endpoint,
application authorization/command, database migration or UI is added in this slice.

The complete required-runtime Linux suite **passed 780 tests**, 491.86s, no skips
and two existing dependency deprecations. Owned run
`reawote-packaging-1eaeac8c0c5346c88da20f0ac6792bc6`, immutable image
`sha256:2e1f3ede6fc621687dd9329d8554d40ce2505efc54269c69a4e324eb2620f1f6`.
This final complete run also includes both cleanup slices and their corrected
test fixtures documented below. The owned container was removed; image retained.
New scenarios verify exact recovery after actual process deaths at six removal/
commit boundaries, active-reader exclusion, corruption/replacement refusal,
strict journal validation and unchanged synthetic source files.
Next integration is specified in [the application plan](packaging-retirement-integration-plan.md).

### Incomplete retention cleanup

The worker now verifies and removes only proven incomplete retained incoming copies
after a handled retention failure or explicit reconciliation. It holds the retention
lock, matches the execution-bound artifact root identity and rechecks operation,
root and lock identity during removal. Complete/READY output is preserved/recovered;
unknown, corrupt or ambiguously owned data refuse cleanup. The old journal/proof
remains unchanged until a separately authorized regenerated attempt records history.
Verification/removal uses a maximum 120-second additional cleanup budget.

New cases cover partial and complete copies, fixed request/plan/root bindings, lock
contention, replacement attacks, expired budgets and real process death during file
removal. The complete required-runtime Linux suite finished **740 passed / 1 failed**,
424.01s, no skips, two existing dependency warnings. Owned run
`reawote-packaging-650806ebf0e043f7aaf914e9c939bc54`, immutable image
`sha256:cfdef294a673f215932931e4806de448b7d358d1f0c401efea45646b556e2627`.
The new execution test's unknown-file fixture used default public permissions and
therefore hit the earlier UNSAFE guard instead of UNEXPECTED_FILE. It now creates
that sentinel with 0600 permissions, preserving the exact expected rejection and
all cleanup/source/history assertions. Application code is unchanged after that run.
All affected cleanup/store/execution/dispatch/stage tests then **181 passed**,
228.08s, no skips, in `reawote-retention-62ee3e055ce040aeac4839293f9075cd`, image
`sha256:9a1861c9367355f4e5560ef1d34f71fe62fa7034f4d7b14e856ba214d27850c9`.

The updated production-runtime HTTP/conversion/restart/proof-bound download/offline
replay/ordered closure smoke passed in owned
`reawote-packaging-service-42b736787ee3416ebde89190e20db264:runtime`.
Owned containers were removed; images retained. No database migration, HTTP endpoint,
production or external write is added. This checkpoint accompanies that verified
cleanup commit; read the current branch tip for its hash.

### Packaging failure cleanup

Handled staging/conversion/retention errors now attempt guarded removal of the
durably owned attempt workspace under the existing execution lease. Unknown files,
changed roots and unrecorded ownership still refuse cleanup. Source/master/PREVIEW
and retained output are outside this removal. The durable WORKING/RETAINED journal
is preserved until explicit reconciliation determines whether output committed;
an error does not invent a retryable or completed result.

New Linux fault cases cover immediate cleanup, exhausted byte limits, committed
retention followed by a lost return/journal error, exact ordered replay and preserved
ambiguous workspaces. Verification:

- Complete Linux required-runtime suite: **717 passed / 2 failed**, 387.31s,
  no skips, two existing dependency warnings. Owned run
  `reawote-packaging-a75c916c29ed42a094772f0bb05165de`, image
  `sha256:1e42ac69b667506b9b3178660024bd44586f9a44872bb4ac1d32d87d722ceae7`.
  Both failures were new test expectations for injected OS errors: the existing
  assembly context maps them to `PACKAGING_ASSEMBLY_FAILED`, not the generic
  execution code. Corrected the exact expected code; application code unchanged.
- All affected execution/dispatch tests then **81 passed**, 112.23s, no skips.
  Owned run `reawote-cleanup-d06a432472eb48a89569045fdaab264e`, image
  `sha256:4c11c1b203dcd3c6fae0f4fb84455e446e395a6173383f6e0f0a25d3d3552600`.
- Actual production-runtime HTTP/conversion/restart/proof-bound download/offline
  replay and ordered closure smoke passed, owned runtime
  `reawote-packaging-service-0c32b84a259947f5bfa1af58ac7445ae:runtime`.
- All **11 offline runner-isolation checks passed**. Test containers removed by
  ownership-checked runners; images retained. No database, migration or frontend
  change in this slice. Tests used synthetic inputs and no network at runtime.

Earlier packaging/GCS docs have also been reconciled with the implemented job,
service, download, staging and credential layers. Live verification and retained
artifact cleanup remain explicitly separate gaps.

### Newly completed

Account-profile creation and role/active updates now use the same ordinary
command controller and in-memory registry as other record forms. Pending state
is bound to kind/action/target, not only the shared page URL. It survives navigation,
blocks another ordinary write for the same actor, and offers exact retry or GET
recovery. Current account data are refreshed after success; later administrator
edits are preserved. Account changes retire initial reads and late callbacks.
The actual administration route is `/settings/users`; USER receipt navigation was
corrected to it before adopting the previously unused USER client path.

No backend or migration change. Password issuance/reset stays in its separate
security workflow and no secret values enter these packets. Self-demotion/disable
and the PROCESSOR creation default remain in place.

Verification:

- Shared-form extraction: **52 focused passed**, 7.15s. Initial lint rejected
  reading a ref during render and mutating a memo object used as state. The
  controller now uses immutable lifetime symbols and effect/event-only refs;
  lint passed with the rules intact.
- New account scenarios + existing account/form/receipt cases: **26 passed**, 3.44s.
  Includes unknown creates, target-bound role retry after a later edit, in-app
  navigation, cross-form blocking, actor changes, late completion/reads and no
  dispatch after unmount during digest preparation.
- Full frontend: **891 passed**, 24.82s; lint, build (2.07s) and E2E TypeScript
  passed. The browser suite passed **23 fresh + 23 retained**, 1.6m/54.9s, run
  `9335edd9-3c53-4efb-bdd6-f58157a2192f`. It includes real lost responses after
  profile create and role/status update, explicit GET recovery, exact replay,
  in-app navigation and preserved later administrator edits after restart.
  Four create/update desktop/mobile screenshots were inspected; 390px layouts
  have no horizontal overflow. Owned cleanup and protected-resource checks passed.
- Initial browser run `3f2e0911-2c37-4f8c-b7ca-c18a5a0b6a19`: **22 passed / 1
  failed**, 2.4m; retained pass did not run. Profile creation and GET recovery
  succeeded, but the new test's exact label lookup did not locate the nested role
  select. The snapshot showed the correctly named enabled combobox. The test now
  selects that exact accessible combobox; role/status assertions are unchanged.
  E2E TypeScript and the fresh/retained run above then passed.

Last pushed ordinary-command evidence: PostgreSQL **418 passed** (auth 27/27),
Linux affected regression **346 passed**, prior frontend **884 passed**, actual
browser **22 fresh + 22 retained**. Exact run/image identities, earlier failures,
corrections and visual evidence remain in [historical checkpoints](autonomous-pbr-history.md).
Migrations through **0025 are immutable**.

## Scope and implementation status

The adopted autonomous request authorizes this isolated branch, additive
migrations, synthetic tests and own-branch pushes. Main, deleted `feature/auth-ui`,
original checkout/work, NAS writes, backups/restore resources and production
databases are excluded. The current user separately authorized reading the R:
copy and importing catalog references into a new isolated test DB. No merge/deploy
or real external write has been performed.

Implemented and tested within documented contracts:

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
- Configurable GCS staging transport, bounded streams, immutable staging jobs,
  explicit start/recovery/abandon UI and opt-in renewable ADC credentials (`0ff994c`).
- Disabled-by-default Notion comparison and reviewed selective local adoption,
  request-bound recovery, immutable company history and UI.
- Resource/account audit with immutable PostgreSQL ledgers and ADMIN paged UI;
  older material/catalog histories now have cursor continuation as well.
- ADMIN material archive/restore (`e9964c5`, `221c9c0`) with preserved identities,
  immutable evidence, current-work exclusion and uncertain-result recovery. Any
  prior external staging dispatch blocks lifecycle changes pending reconciliation.
- Atomic ordinary-write receipts and lost-response recovery in the shared record
  and account-profile forms; current-role/target checks apply to replay and read
  recovery. Account credentials keep their separate security contract.
- Explicit proof-bound local-copy retirement, immutable schema 0025 evidence,
  ADMIN controls, download/staging exclusion, restart recovery and retained receipts.
  Removal stays disabled by default; it never marks a material published.

These are bounded implementations, not a claim of production-ready completion.
The README links the individual feature/operations contracts.

## Next work and real blockers

1. Finish the [real R: acceptance](historical-r100-acceptance.md): read-only NAS
   worker access, actual gallery/texture checks and explicit mapping of remaining
   Excel properties. The first 100 catalog records already exist. Implement the
   new name/folder and manufacturer-directory rules before enabling source writes.
2. Verify actual importer contract, golden material outputs and manual publication
   confirmation. The later complete production workbook and importer fixtures
   are unavailable; do not invent live verification.
3. Verify isolated live GCS/Notion/AI only after separately authorized targets and
  access are supplied. Credential contract tests do not substitute for live checks.
4. Review production topology, representative workload and backup custody before
   any separately approved deployment. Multi-gigabyte throughput remains unverified.

3D/HDRI remain later scope. Backups are unchanged; off-machine custody is unconfirmed.
Draft PR has not been created: no available GitHub CLI/PR connector was found.
The owned remote branch is the review artifact in the meantime.

## Resume and verify safely

Read this checkpoint, inspect branch/HEAD/status and compare remote main with the
base. Preserve unrelated work. Never use the deleted experimental branch.

- PostgreSQL: `./scripts/test.ps1 -PostgresqlOnly` with local Docker CLI on PATH.
  It owns a new GUID namespace and requires all auth tests; never substitute
  original/demo databases. It removes containers/network and retains own image/volume.
- Linux: immutable owned image, nonroot/read-only/no network or host mounts;
  use relevant suites and record image identity. Worker changes require actual
  Linux worker-boundary tests and the isolated packaging runtime where applicable.
- Frontend from `frontend`: `npm test`, `npm run lint`, `npm run build`, local
  `node_modules/.bin/tsc --project tsconfig.e2e.json --noEmit`.
- Browser: only `./scripts/test-demo-e2e.ps1` with
  `E2E_PROJECT_NAME=reawote-e2e-auto-01a0a64d`, `E2E_KEEP_SUCCESS_ARTIFACTS=1`.
  Do not overlap that namespace or change protected resources during its snapshot.
  Collect fresh and retained passes; inspect changed desktop/mobile UI.

Commit explicit owned files after checks, then push only
`HEAD:refs/heads/codex/autonomous-pbr-completion` to the verified repository remote.
Continue the next unblocked slice; one completed feature is not completion of the task.
