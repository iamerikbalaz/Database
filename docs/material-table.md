# Materials: editable list (2026-09-25)

## User workflow

- List and Gallery use the same material filters. The list has a small primary
  preview (FABRIC_1.png, then SPHERE_1.png, then the remaining PNGs). It reuses the
  bounded thumbnail queue/cache and loads only near the viewport.
- Preview and material identity stay pinned on desktop. Properties controls
  visibility, order and widths of the other columns. Only display preferences
  persist in local storage; private row values and images do not.
- Project, Processor, Status, Checked and Published save directly from their
  controls. Note supports 10,000 characters, newlines, an explicit Save button
  and Ctrl/Cmd+Enter. Escape discards its draft. Unrelated cell saves preserve a
  pending Note draft. Refresh/navigation discards unsaved local drafts.
- The material identity link remains available. Category and Published brand
  choices open the existing controlled identity planner for linked folders.
  Unlinked, unpublished materials in progress can change identity after a
  database-only confirmation, with numbering reservations and identity history.
  Active operations and existing brand collections still block unsafe transfers.
- Bulk selection covers every record returned by the current filters, including
  offscreen rows. The current API returns the complete filtered list without
  pagination. No server-side implicit “all records” mutation is performed.
  Bulk fields: Project, Processor, Status, Checked, Published and Note. Note
  replacement is stated explicitly in the review. Identity changes need their
  individual source plans.

## Meaning of the fields

| Field | Values / behavior |
| --- | --- |
| Status | In progress / Done; one workflow field |
| Checked | no / OK / Correction; explicitly human review |
| Published | Yes / No; manual evidence, no upload or importer action |
| Note | Optional multiline text; independent of publication description |

A transition to Done still reads and verifies the linked source folder and
records its metadata snapshot. Missing metadata.txt remains nonblocking for a
safe folder. A new Done transition resets Checked to no. OK requires Done.
Correction reopens the material as In progress. Ordinary changes that invalidate
a source/content review also reset Checked; automatic technical validation never
grants human OK. Note and Published edits do not invalidate approvals.

The old validation/publication state columns remain internal pipeline data and
historical evidence. The main list/detail present the simplified human fields;
the optional File check column and specialist technical panels retain diagnostics.

Administrators and production leads can edit all these properties. Processors
can change Status and Note on their own assigned materials. Leadership is read-only.
Every endpoint rechecks current authorization, archival state and active-operation
locks, independently of what the browser enables.

## Write and recovery contract

`PATCH /api/materials/{id}/table` accepts exactly one changed property plus the
row's `expected_updated_at` and a nonzero `Idempotency-Key` header. Writes compare
the current timestamp under a row lock. Source preflight runs outside database
transactions; final authorization and the material revision are checked again.

Each successful write atomically records the material snapshot, resource history
and an actor-bound receipt. Retrying the same packet returns the original receipt
and cannot create a second metadata snapshot or allocate a second material number.
Receipts remain readable through the existing resource-command recovery endpoint.

Bulk review freezes IDs, prior versions, proposed values and one key per row.
It executes serially, records per-row results, and stops on unknown outcomes.
Retry resumes the exact pending packet and skips successful/rejected items. A
later 4xx cannot turn an earlier unknown outcome into proof of rejection. A known
preflight-service failure is explicitly marked as not written and stops the
remaining rows. Stop waits for the current request; leaving the page/session
prevents further requests. Browser reload does not automatically restart a batch.
Before-unload warns while a batch is open. The report stays in page memory;
durable evidence is the server history/receipts. Refresh reapplies the current
filters after successful edits.

## Categories and migration

The supplied `TEXTURE-CATEGORIES_2026.xlsx`, sheet List1, A1:B75, supplies 18
groups and 57 subcategories. The checked-in vocabulary preserves row order,
spelling and codes, including the unused F05/O08 gaps. Duplicate display names
use their parent path, e.g. `K03 · Metal / Tiles`. The frontend JSON is the same
versioned data as `backend/app/texture_categories_2026.json`.

Migration 0027 adds Checked (default no), Note (default NULL), an index and a
database constraint. It extends the existing exact-snapshot PostgreSQL triggers
without rewriting old immutable history or receipts. Old receipts retain their
original fields and hashes. It seeds canonical online category paths using
stable UUIDs, preserving matching existing values, activity and assignments.
Custom/legacy categories remain intact. No material gets an inferred online
category assignment. Main storage category and online publication categories
remain separate concepts; both show the supplied codes where known.

Downgrade refuses populated human fields or history written in the new format.
Seeded categories are retained because they may already be referenced. A normal
upgrade leaves material names, folder links, sequence numbers and existing data
unchanged. No source file access or writes are performed by the migration.

The R100 local acceptance app still uses a dated derived-preview snapshot and
has no live NAS worker connection. Ordinary database cell edits work there;
Done preflight and linked identity planning require that connection. Source
mutations, packaging and external publication remain disabled in that instance.
