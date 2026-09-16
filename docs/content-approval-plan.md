# Next slice: approval of publication content

The catalog slice stores manual drafts. Before a draft can enter a publication
snapshot, add an explicit human decision for the exact content revision and its
current material, brand and source-review context.

## Proposed implementation

- New migration 0011: append-only content approval records referencing an existing
  material content revision. Store actor, timestamp, context hash, reviewed
  snapshot, note and explicit warning acknowledgment. Do not rewrite 0010.
- A read-only content review endpoint returns the current context, concrete
  blocking findings/warnings and any matching current approval. Old decisions
  remain in history when content, material identity, source review or vocabulary
  availability changes.
- Leadership/admin may approve. Assigned processors and leads retain draft
  editing privileges. This conservative assumption follows the existing
  publication-approval role boundary; it does not grant new editing rights.
- Require an idempotency key and the exact displayed context hash/revision.
  Recheck under the material lock and a brand read lock; catalog mutations already
  serialize through the domain gate. No external calls or source writes occur.
- Missing credits/categories, an inactive brand/value or wrong-brand collection
  block approval. Empty descriptions/tags are explicit warnings requiring
  acknowledgment and a note. These content checks do not replace technical checks,
  production Done or publication approval.
- Publication jobs must require both current content and material approvals before
  freezing inputs. The existing production/technical approval endpoints alone
  still do not publish anything.

## Verification

Real role/assignment/CSRF tests; no-op/replay and stale-context rejection; catalog,
brand, material and source changes invalidate current approval without deleting
history; PostgreSQL competing reviewers/editors and immutable records; fresh and
prior-0010 migration; UI unknown-outcome retry; real E2E approval retained through
restart and invalidated by an actual content edit.

AI generation/import provenance remains subsequent work. No generated content or
external integration success should be simulated by the production application.
