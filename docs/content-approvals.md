# Publication content approval

Migration 0011 adds immutable decisions for exact saved publication content.
It does not publish, create a job, upload files or call an external service.

## Product assumptions and behavior

- Leadership and administrators may approve, following the existing publication
  approval boundary. Assigned processors and production leads keep draft editing
  rights but receive no extra approval privilege. All routes require active
  authenticated sessions; writes require CSRF and the trusted Origin.
- Each decision references an existing content revision and stores the complete
  reviewed snapshot, its canonical SHA-256 hash, actor, timestamp, note and warning
  acknowledgment. History cannot be updated, deleted or truncated in PostgreSQL.
- Missing credits or categories, inactive catalog values/brand, and a collection
  owned by another brand block approval. Empty description/tags require explicit
  acknowledgment and a note. Credits zero is valid. Collections are optional.
- `APPROVED` is derived from a matching decision for the current context. The
  context includes the saved content and vocabulary versions, material identity,
  name/assignment/project, brand export fields, and observed source generation/hash.
  It excludes check timestamps, so an identical source scan retains approval.
- Relevant material/content/source/catalog changes invalidate current decisions
  without deleting them. Brand PATCH now invalidates affected materials under the
  exclusive domain gate; changing a brand and restoring the old values does not
  resurrect an earlier approval. Identical saves/PATCHes retain current approval.
- Content can be reviewed before a source scan. The UI explicitly explains that
  the later scan will change context and require a new decision. To avoid repeated
  review, finish content editing and source/technical checks before approving.
  A content decision never substitutes for production Done, technical approval or
  publication approval. Future publication jobs must validate all these conditions
  and freeze the exact matching inputs together.
- The frontend displays the saved snapshot in a separate confirmation section.
  Unsaved editor changes are excluded. Unknown transport outcomes freeze decision
  inputs and retry the exact idempotency key and payload. A successful replay can
  return an old decision; the UI reloads current status before displaying approval.

## API and transaction boundaries

- `GET /api/materials/{id}/content-review`: current snapshot/hash, requirements,
  warnings, derived status and matching approval.
- `POST /api/materials/{id}/content/approve`: expected content revision and context
  hash, UUID idempotency key, optional note and explicit warning acknowledgment.
- `GET /api/materials/{id}/content-approvals`: latest 100 immutable decisions with
  reviewed snapshots. Pagination remains backlog.
- `GET /api/materials/{id}/content` now derives `APPROVED` for a current decision;
  old content revision snapshots remain drafts as originally recorded.

Approval takes the shared domain/account gate, authenticates again, locks the
material and then reads the brand under `FOR SHARE`. It recomputes context before
inserting the decision and request audit in one transaction. Catalog and brand
changes take the exclusive gate before domain locks. Active source operations
block a new decision; replay remains possible without restarting an operation.
No external IO occurs while these locks are held. A uniqueness constraint prevents
duplicate decisions per material/context; a composite FK binds the exact saved
revision. Validation failures never echo submitted notes or arbitrary fields.

## Migration and rollback

0011 adds only `material_content_approvals` and its index/constraints/append-only
trigger. Existing migration files through 0010 remain unchanged. Existing drafts
start unapproved, with their data and history preserved. Downgrade to 0010 removes
approval decisions only; retain/export them before deliberate rollback. Use the
0010 application version with the downgraded schema. Files, source journals,
number reservations and prior material/content history are unaffected.

No production migration or deployment has been performed. AI provenance,
publication jobs and live integration verification remain separate backlog.
