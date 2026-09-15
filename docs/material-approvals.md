# Revision-bound technical and publication approvals

Migration `20260915_0008` adds immutable `material_technical_checks` and
`material_approvals`, plus the current technical-check pointer on review state.
Existing rows receive no approval. PostgreSQL rejects UPDATE, DELETE and
TRUNCATE of both histories; foreign keys bind decisions to the same material,
generation and revision as their technical check.

## Workflow and authorization

| Action | Allowed role |
| --- | --- |
| Read reports and approvals | Existing material visibility for all four roles |
| Run technical checks | Assigned processor, production lead, administrator |
| Grant technical approval | Production lead, administrator |
| Grant publication approval | Leadership, administrator |

Production `DONE` is required for human approval. Checks may also run while a
material is in progress. Done itself still permits missing or invalid metadata.
Technical errors block approval. Nonblocking warnings require the reviewer to
acknowledge them and provide a note; this is a conservative product assumption.
Publication approval additionally requires a current technical approval. These
decisions grant no upload, external-write or deployment permission.

The technical and publication reviewers may be the same administrator; the
handoff specifies two decisions, not a mandatory two-person separation. If the
organization later requires separation, enforce it on the server before enabling
publication jobs. Roles, active sessions, forced-password status, CSRF and material
assignment are enforced by the backend, including after each worker call.

## API

Routes below `/api/materials/{id}`:

- `GET /technical-review`: current review, technical report or null, and only
  approvals matching the current generation and revision.
- `POST /technical-review/run`: UUID `idempotency_key`, `expected_generation`.
- `POST /approvals`: the same fields plus `kind` (TECHNICAL or PUBLICATION),
  `expected_revision_hash`, current `technical_check_id`, optional `note`
  (1–2000 characters) and boolean `warnings_acknowledged`.

The worker performs real bounded image decoding and inventories the complete
source tree. The backend validates all image hashes against the inventory and
refuses contradictory success, missing proofs, wrong formats/dimensions,
unsupported report versions or unbounded/unknown data. Reports contain facts and
safe finding codes, not source metadata contents or decoder exception messages.

Every human approval triggers a fresh worker check outside database locks. The
final transaction locks the material, rechecks access and the expected material
state, then compares both the source revision and technical findings with the
report the reviewer saw. A changed result is persisted for review with HTTP 409,
without an approval. A failed/unavailable scan invalidates current review. The
whole response, including a rejected/failing result, is replayable for the same
actor/key/body. Unknown transport outcomes should retry the original key.

Each successful check records an immutable inventory and technical report.
Identical rechecks keep the generation and existing decisions. A changed report,
changed source, material edit, relink, Done, reopen or failed scan clears current
decisions through generation invalidation. Same file bytes after reopen cannot
revive earlier approvals. History remains in the database and recent events in
`GET /audit`. Full historical report browsing is still a separate backlog item.

No database lock makes a NAS snapshot. These approvals describe observed source
contents; packaging must independently create and verify immutable staged inputs
before consuming them. Catalog fields introduced later must join revision context
and invalidation rules before they may influence approved publication output.

## Verification and rollback

The isolated Docker run for the server slice passed 527 backend unit/API tests,
90 real PostgreSQL tests (mandatory auth 27/27, no skips), 213 Linux worker tests,
and the prior 206 frontend tests plus lint/build. PostgreSQL coverage includes
fresh upgrade, upgrade from 0007 with retained review history, current/heads/check,
downgrade/re-upgrade, append-only/cross-revision constraints, concurrent identical
and competing approvals, and actual API edits/reopen/account disable while the
worker is delayed. HTTP tests cover source/finding changes, failed scans,
least-privilege roles, warning acknowledgment and exact idempotent replay.

Only an explicitly selected isolated/local database has been migrated in this
task. For later authorized rollout, back up the intended database and run the
normal `alembic upgrade head`. Code rollback can leave the additive tables intact.
An explicit `alembic downgrade 20260915_0007` removes approval/check history and
its current pointer; earlier inventory/audit/metadata histories and source files
remain. No production migration or downgrade is authorized by this work.
