# Persisted historical ZIP policy

Migration 0016 adds append-only material policy decisions. Each material has an
initial decision and an ordered chain of administrator overrides. The latest
revision is the saved policy; later observations never replace it automatically.
PostgreSQL rejects UPDATE, DELETE, TRUNCATE, gaps, stale predecessors, same-policy
overrides, changed storage timezones and cross-material inventory references.
ORM history protection also applies in portable tests.

## Selecting and changing a policy

The material detail has an expandable **ZIP packaging policy** panel. It loads
only when opened and keeps any unresolved request mounted when collapsed.

- Leadership or an administrator can save the first rule for a DONE material
  with a successful current technical report. The request binds the exact
  inventory, generation and revision. The stored source context and report hashes
  are revalidated; a malformed or stale report cannot select a policy.
- The initial rule uses the original master folder modification time from the
  saved source inventory, before staging/copying. It stores that observation,
  inventory/report hashes, timezone and midnight boundary as immutable evidence.
- Subsequent selection requests reuse the saved decision without inspecting new
  timestamps, even after reopen or a changed inventory.
- Only an administrator can override. They must review the server preview,
  acknowledge its effect and enter a reason. Confirmation binds the exact
  decision and preview hash. A changed material/review rejects an old preview.
- An override invalidates current review and all associated approvals. Published
  materials keep their published flag and require an update. Existing batch CSV
  and artifacts remain unchanged. The policy's storage timezone stays fixed.
- Current state and paginated history follow ordinary material read permissions
  and processor assignment. Mutation authorization includes CSRF and active
  account/session checks; active identity operations block a new decision.
- Lost responses retain the exact request and idempotency key for retry. Definite
  rejection requires a fresh review; raw server diagnostics are not displayed.

The source inventory's policy is still an observed fact. Current packaging code
requires an explicit policy; the future database job coordinator must pass the
saved policy and snapshot its decision ID, revision and timezone into the job.
This slice creates no packaging job or upload.

## Configuration

Set ZIP_POLICY_TIMEZONE to the same installed IANA timezone on backend and worker.
The shared Compose configuration forwards one value to both; default Europe/Prague.
The backend validates the zone at startup and rejects initial selection when its
classification disagrees with the observed worker policy.

Strictly before local midnight on 4 March 2026 selects
LEGACY_BEFORE_2026_03_04; midnight itself and later selects
CURRENT_ON_OR_AFTER_2026_03_04. Nanosecond timestamps immediately before the
whole-second boundary remain before it. This preserves the current repository
enum and comparison semantics. The process owner still needs to confirm that
business boundary before deployment, as stated in the handoff.

No existing material is automatically classified by the migration. No source
timestamps or files are modified. Changing runtime timezone does not rewrite an
existing policy; a future timezone-change workflow would require a separate
reviewed migration of decisions.

## Endpoints

Under /api/materials/{id}/packaging-policy:

| Method / path | Purpose |
| --- | --- |
| GET | Current saved decision or null. |
| GET /history | Up to 50 decisions, descending revision, optional before cursor. |
| POST /select | First selection or reuse, publication approvers only. |
| POST /override-preview | Exact proposed effect, administrator only. |
| POST /override | Audited successor bound to that preview, administrator only. |

Both mutations use the existing material audit idempotency ledger and row lock.
The database enforces contiguous immutable ancestry independently of ORM hooks.
Evidence contains decision/source digests and facts, without raw metadata or
credentials. A preview stores only the current decision summary, avoiding recursive
copies of earlier evidence.

## Verification / rollback

The initial focused backend suite passed 119 cases. Complete isolated verification
passed 1126 backend and 171 actual PostgreSQL cases (mandatory auth 27/27), including
fresh/prior upgrade, Alembic current/heads/check, immutable/FK/downgrade guards and
real concurrency. No backend or PostgreSQL case was skipped. Existing dependency
warnings remain. The subsequent UI passed 457 frontend tests, build, lint and E2E
TypeScript checks. Test-only Testing Library typing and an unused callback parameter
were corrected without changing validation rules. An initial E2E exposed the
whole-detail refresh closing the panel after a successful save; the callback now
refreshes detail data in place. The final unchanged assertions passed all 17 fresh
and 17 retained-data E2E scenarios. Desktop and 390px screenshots were visually
inspected. The complete final frontend suite again passed 457 tests, lint/build
and E2E TypeScript. Exact project/run identifiers are in autonomous-pbr-progress.md.

Migration 0016 is additive. Empty downgrade to 0015 is supported; any saved policy
refuses downgrade to preserve provenance. Prefer a forward repair once populated.
Existing committed migrations through 0015 are unchanged. No production migration
or publication has been performed. Database execution/attempts, packaging ownership,
GCS/Notion adapters and importer confirmation remain separate unfinished work.
