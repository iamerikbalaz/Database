# Autonomous PBR completion

## Latest checkpoint (2026-09-19, ordinary command recovery)

Owned worktree: `C:\Database\Database\tmp\autonomous-pbr-completion`.
Branch: `codex/autonomous-pbr-completion`. Read `git log -1` for its current tip.
History API is committed as `3eb2abf786d9d9a24c99eaf3448cfed94ed74bd3` and UI as
`8135c976b9ae6f5dc99bb2470a9922eaf4741e67`, both pushed. The Docker path guard fix
is committed as `c3a0d1631f06e372284409da5fe003b472cd0b45`; ordinary command API and
migration 0024 as `249b9e6e3a88495544b8eaaaf44c5ba50241972c`. This checkpoint
accompanies the verified shared-form UI commit; read the branch tip for its hash.
Remote main was last verified at `88a1f99d748d2a0edbb1fce509e13d18bfc03908`.

### Newly completed

Ordinary create/PATCH receipts and shared-form recovery are implemented and tested.
See [resource commands](resource-commands.md). All ten API
routes support actor-bound exact replay; shared company/brand/project/material
forms retain uncertain requests across navigation. The separate account-profile
UI remains to be migrated, and legacy keyless calls keep their old semantics.

Current evidence for this slice:

- Local command/access regression: **38 passed**, 32.35s, two warnings.
- Full frontend: **884 passed**, 26.92s. Final UUID case-normalization assertion:
  **8 focused passed**, 32ms; final lint, build (2.41s) and E2E TypeScript passed.
- Initial PostgreSQL run: **415 passed / 3 failed**, 548.77s. The new material
  fixture directly inserted material 1 while leaving its brand counter at 1.
  Correcting that synthetic fixture to 2 preserved the allocation assertions;
  focused PostgreSQL/auth then passed **48 tests**, 62.32s, no skips (auth 27/27).
  This earlier run predates raw-input digest binding. Full fresh PostgreSQL then
  passed **418 tests**, 542.79s, no skips, three warnings, auth **27/27**, in
  `reawote-test-31abd5dcd45a4111a90aafea5365578b`; inspected image
  `b7f1a411d092c600adf0244195ab0b24bbdf900010ddd467a97156f2d6340386`.
  Owned containers/network removed; own image/volume retained.
- A malformed non-JSON request test caught an uncaught JSON decoding error in the
  new dependency. It now returns bounded 422 without reflecting input; the final
  local 38-case run above includes all three malformed-body cases. The PostgreSQL
  PostgreSQL image predates only this validation-boundary correction.
- Final-code Linux affected regression: **346 passed**, 226.93s, no skips, two
  warnings. Owned nonroot/read-only/networkless container
  `reawote-resource-linux-eef24853a05a40fd8366160586e76b10` auto-removed; image
  `049ab3e27775ecce5ce6a356d41277f533e8b8772e6c1f1ea3d2a3223918c6a9` retained.
- Actual browser suite passed **22 fresh + 22 retained**, 1.6m/55.7s, run
  `4318c97f-2d91-4672-a12a-569960d9c22a`. The new case commits one material,
  loses its response, retains the original form across navigation, reads its exact
  receipt without another POST and replays it after restart without replacing a
  later edit. Protected resources remained unchanged; owned cleanup passed.
  Playwright's retained pass removed fresh-only pending-save screenshots, so the
  test now stores those two PNGs in the same guarded run artifact root outside
  Playwright's resettable output directory. E2E TypeScript passed; the follow-up
  run `c979b17e-e07a-477f-9d5a-62f7135ddc2e` passed **22 fresh + 22 retained**,
  1.6m/54.8s. Desktop and 390px mobile screenshots were inspected; no horizontal
  overflow. Both runs passed owned cleanup and protected-resource comparisons.
  Initial run `82ce7de3-56fe-43b3-abd4-ab1ebcebbff1` ran **no browser scenarios**:
  the packaging source guard rejected Docker Desktop's inspected
  `/run/desktop/mnt/host/c/...` mapping of its exact synthetic Windows root.
  The guard now accepts that full, exact local-drive mapping without prefix
  stripping/containment shortcuts; confinement, labels, volume and network checks
  remain mandatory. Seven foreign/relative/ambiguous mappings are rejected.
  Packaging isolation helpers and the full demo E2E helper safety suite passed.
  An initial shell wrapper incorrectly checked stale LASTEXITCODE after a pure
  PowerShell test; the corrected Stop-on-error invocation passed both suites.

Migrations through **0024 are immutable**. Earlier history pagination, archive,
GCS, account-history and database-error-boundary evidence, including intermediate
failures and corrections, remains in [historical checkpoints](autonomous-pbr-history.md).

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
  forms; current-role/target checks apply to replay and read recovery.

These are bounded implementations, not a claim of production-ready completion.
The README links the individual feature/operations contracts.

## Next work and real blockers

1. Extend the separate account-profile administration forms to the same
   recovery contract ([bounded plan](account-profile-recovery-plan.md)). Legacy
   requests without keys have no exact replay guarantee.
2. Finish derived-artifact cleanup lifecycle and operational/final review docs.
   Temporary workspaces already have bounded ownership/journal-based cleanup;
   accepted retained ZIP expiry/removal needs a separate explicit contract.
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
