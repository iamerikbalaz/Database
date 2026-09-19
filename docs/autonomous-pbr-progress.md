# Autonomous PBR completion

## Latest checkpoint (2026-09-19, private retirement boundary)

Owned worktree: `C:\Database\Database\tmp\autonomous-pbr-completion`.
Branch: `codex/autonomous-pbr-completion`. Previous pushed checkpoint:
`9eb496cff885f4bad7e667f94701596caacd86e8` (durable storage retirement).
API/migration 0024: `249b9e6e3a88495544b8eaaaf44c5ba50241972c`; Docker source
mapping guard: `c3a0d1631f06e372284409da5fe003b472cd0b45`. Remote main last
verified unchanged at `88a1f99d748d2a0edbb1fce509e13d18bfc03908` during that push.
Shared controller extraction is committed as `9957a0ef8729f23175412ac2f295886c35d76bc2`.
Account profiles (`6c83a33`), temporary error cleanup (`7565a1c`), incomplete retained
copy cleanup (`e5679b6`) and reconciled docs have been pushed. Remote main remains
unchanged at the stated base.

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
The additive 0025 database schema is the next draft and is excluded from this commit.

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
Migrations through **0024 are immutable**.

## Scope and implementation status

The adopted autonomous request authorizes this isolated branch, additive
migrations, synthetic tests and own-branch pushes. Main, deleted `feature/auth-ui`,
original checkout/work, NAS, backups/restore resources and production databases
are excluded. No merge/deploy or real external write has been performed.

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

These are bounded implementations, not a claim of production-ready completion.
The README links the individual feature/operations contracts.

## Next work and real blockers

1. Add application retirement provenance, ownership/download/staging gates and UI.
   Storage and the opt-in private service/client are verified; new database/API/UI
   work remains. Temporary workspaces and proven incomplete retention have guarded
   cleanup. Accepted output requires explicit proof-bound retirement.
2. Complete operational/final review docs after the application boundary is tested.
3. Verify actual importer contract, golden material outputs, manual publication
   confirmation and realistic historical workbook/source compatibility. Production
   inputs/importer fixtures are unavailable; do not invent live verification.
4. Verify isolated live GCS/Notion/AI only after separately authorized targets and
   access are supplied. Credential contract tests do not substitute for live checks.

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
