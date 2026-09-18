# Autonomous PBR completion

## Latest checkpoint (2026-09-18, history pagination)

Owned worktree: `C:\Database\Database\tmp\autonomous-pbr-completion`.
Branch: `codex/autonomous-pbr-completion`. Read `git log -1` for its current tip.
History API is committed as `3eb2abf786d9d9a24c99eaf3448cfed94ed74bd3`; its
verified UI and this checkpoint are ready for the following commit. Previous
pushed tip was `0ff994c4491bc194da78f129ba332ef5b2e978f6` (GCS credentials).
Remote main was last verified at `88a1f99d748d2a0edbb1fce509e13d18bfc03908`.

### Newly completed

Six existing material/catalog history routes now accept bounded UUID cursors
without changing their legacy JSON shapes. Timestamp ties are ordered by UUID;
content history follows revision order. Current roles/assignment/archive checks
precede cursor lookup. Unknown and cross-material cursors have the same response.
Source, identity, content and content-approval panels offer Older/Latest controls;
older pages never replace current operation, approval or editable draft state.
Pending history reads are scoped to actor/target; account changes also retire the
initial reads. See [history pagination](history-pagination.md).

Verification:

- New local API cases: **17 passed**, 18.94s, two dependency warnings.
- Full isolated PostgreSQL: **397 passed**, 512.34s, three warnings, no skips;
  mandatory auth gate **27/27**. Owned project
  `reawote-test-d59157bf761b4d969dd9a9f5f78739df`, immutable image ID
  `80f5c53bb0a10bbe16997f4dcdaeb773c945448b967be9ad09f1459dafa6aa2b`.
  Includes all five new PostgreSQL material cursor cases and existing schema,
  upgrade/downgrade/concurrency tests. Containers/network removed; own volume retained.
- Linux affected API/access/lifecycle regression in that image: **149 passed**,
  148.97s, no skips, two warnings. Nonroot, read-only, no network/host mounts;
  own container `reawote-history-linux-0ac84c36b3db4f998c1a88ff083993e1` auto-removed.
  An initial invocation used an unavailable build-config hash and ran no tests;
  the successful run used the inspected actual image ID above.
- Full frontend: **867 passed**, 25.60s. Final account-scope/active-owner additions:
  **64 focused passed**, 3.72s. Lint, TypeScript build, Vite build (2.00s) and
  E2E TypeScript all passed. Initial two approval tests caught a removed history
  setter still referenced after success; fixed, preserving the existing assertions.
  Initial test-fixture TypeScript errors were also fixed before the final build.
- Actual browser suite: **21 fresh + 21 retained passed**, 1.6m/55.2s, run
  `678e7561-960a-4cac-b363-e1fd94646ae8`. The new scenario creates 103 content
  revisions through the real API and reaches the oldest three after restart,
  preserving current draft 103. Desktop/390px mobile screenshots inspected;
  no horizontal overflow. Owned cleanup and protected-resource checks passed.

No migration/data rewrite for pagination. Migrations through **0023 are immutable**.
Previous archive/restore, GCS, account-history and database-error-boundary evidence,
including incomplete/failed intermediate runs and their corrections, is preserved
in [historical checkpoints](autonomous-pbr-history.md).

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

These are bounded implementations, not a claim of production-ready completion.
The README links the individual feature/operations contracts.

## Next work and real blockers

1. Implement ordinary create/PATCH lost-response recovery. Concrete next slice:
   [resource command plan](resource-command-plan.md). Legacy requests without keys
   currently have no exact replay contract; form resubmission can duplicate writes.
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
