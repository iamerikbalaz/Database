# Immutable publication preparation

This implements **CSV preparation**, not packaging, uploading or online publication.
Migration `20260917_0015` adds `publication_batches` and `publication_batch_items`.
It has no filesystem or external service effects. Materials keep their publication state.

## Operator flow

Sign in as ADMIN or LEADERSHIP and open **Publication** (`/publication`). Search DONE
materials by name/project, explicitly select 1–100, and review their export values.
Resolve any blocking findings in the material screens and reload the preview.
Enter a reason, confirm the reviewed values, acknowledge any warnings, then save.
History supports bounded pages and downloading the exact saved CSV.

The file is UTF-8 with BOM, semicolons and CRLF; see `publication-csv.md`. Browser
download checks its SHA-256, response length, size bound and header before offering
the file. Formula-like values keep their approved text for importer compatibility;
the UI warns that spreadsheets may execute formulas. Use the intended importer or
a plain text viewer. This preparation step has not been validated against the live
online importer and must not be treated as authorization to publish.

A lost save response freezes the controls and offers **Retry same batch request**.
It preserves the exact request and idempotency key in component memory. Stay on
the page until resolved. If the tab/session is lost, inspect batch history before
starting another preparation; the in-memory request cannot be recovered on reload.
Later edits never rewrite a saved batch. Historical downloads remain available to
authorized roles even after current approvals have become stale.

## API and concurrency

- `POST /api/publication-batches/preview`: read-only preview (documented separately).
- `POST /api/publication-batches`: `material_ids`, `expected_preview_hash`, UUID
  `idempotency_key`, nonempty `reason`, boolean `warnings_acknowledged`; returns 201.
- `GET /api/publication-batches?after=<UUID>&limit=20`: descending timestamp/UUID
  cursor, maximum 50. Browser requests 20.
- `GET /api/publication-batches/{id}`: immutable rows and approval references.
- `GET /api/publication-batches/{id}/csv`: exact stored bytes, UUID filename and
  `X-Content-SHA256`; returns an integrity error if stored bytes/hash disagree.

Every route uses actual account/session authorization. Writes require same-origin
CSRF protection. Preview and create use the shared account/catalog gate and lock
selected materials in UUID order. Creation repeats the preview under those locks;
changed inputs/approvals reject the stale preview. All rows, CSV, proof references
and per-material audit events commit together, or none do. Exact actor-scoped
replays return the original batch even after later material edits; changing a
request body under the same key fails. Reversing selection order is equivalent.
Session expiry is rechecked after domain work before recording new data.

Private snapshots bind workflow, identity, inventory, normalized metadata source,
technical report, all three human decisions and content context. API responses
omit source paths and raw metadata. Composite foreign keys prevent cross-material
approval/check/metadata references. ORM and PostgreSQL triggers reject UPDATE,
DELETE and TRUNCATE for both provenance tables. Stored status is always PREPARED.

## Migration and rollback

Apply the additive migration only to an explicitly selected application database
through the existing migration procedure. No production migration has been run.
Tests cover fresh upgrade, 0014→0015 with retained records, Alembic current/heads/check,
empty downgrade/reupgrade, provenance constraints and concurrent writes.
An empty 0015 can downgrade to 0014. A populated 0015 deliberately refuses downgrade:
preserve the database/schema and use a forward correction or reviewed restore.
An older application can ignore these additive tables; do not delete their history.

## Remaining execution boundary

Verification: full isolated project `reawote-test-2fd633be5ba54892a6638e9e4c2d9bf7`
passed 1100 backend, 163 actual PostgreSQL (27/27 auth gate), 354 Linux worker and
436 frontend tests, lint/build, without skips. E2E `d4c69daa-90c2-4679-b750-c3601d47c8a1`
passed 17 fresh and 17 retained scenarios, including a lost successful response,
exact replay and byte-identical historical download after later content edits.
Saved-batch desktop/390px screenshots were inspected; protected resources stayed
unchanged and owned cleanup passed.

Preparation validates the currently recorded approved inventory, without reading
the NAS or claiming files are unchanged since that inventory. The implemented
[packaging actions](packaging-actions.md) freshly revalidate source bytes and current
approvals before execution and acceptance, then bind the proof to this batch.
[GCS staging jobs](gcs-upload-jobs.md) add reviewed plans, ownership, guarded transfer
and immutable receipts. [Explicit local-copy retirement](packaging-retirement-api.md)
implements proof-bound removal and recovery with a disabled-by-default ADMIN
control. It does not confirm an online import or run automatically after one.
Live importer verification and online-import confirmation remain separate work.
CSV preparation alone must never set `is_published` or report an upload.
