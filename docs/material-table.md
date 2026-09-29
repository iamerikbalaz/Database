# Materials: editable list and gallery (2026-09-28)

## User workflow

- List and Gallery use the same material filters. The list has a small primary
  preview (FABRIC_1.png, then SPHERE_1.png, then the remaining PNGs). It reuses the
  bounded thumbnail queue/cache and loads only near the viewport.
- Preview and material identity stay pinned on desktop. Properties controls
  visibility of the other columns through the same disclosure and checkboxes as
  Projects, Companies and Catalog. Column order and widths use fixed defaults. Only display preferences
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
- The table header checkbox selects or clears every filtered row; duplicate
  selection buttons are omitted.
- Row highlighting is separate from checkbox selection. Click a noninteractive
  part of a row to highlight it, Shift-click to highlight a contiguous visible
  range, Ctrl/Cmd-click to add or remove individual rows, or Ctrl/Cmd+Shift-click
  to add a range. **Select highlighted** adds those rows to the checked selection.
  Inline controls, checkboxes and material links retain their normal behavior.
- List and Gallery share checked material IDs. Gallery cards expose the same
  checkboxes and header selection. Switching views retains selection; changing
  filters clears it. A refresh prunes IDs no longer in the result, preventing
  hidden records from entering a later bulk operation.
- Bulk selection covers every record returned by the current filters, including
  offscreen rows. The current API returns the complete filtered list without
  pagination. No server-side implicit “all records” mutation is performed.
  Bulk fields: Project, Processor, Status, Checked, Published and Note. Note
  replacement is stated explicitly in the review. Identity changes need their
  individual source plans.

## Search and color filters

The shared search matches material name, technical identity and Note. Matching
is case-insensitive and literal, so a note such as `#Autumn #Review_100%` can be
found using either tag without treating `_` or `%` as wildcards. Existing filters
and processor assignment restrictions still apply.

Color is a multi-select combobox containing descriptive names and colored
squares. Its 18 HEX values match the [REAWOTE texture gallery](https://reawote.com/textures)
palette verified on 2026-09-28; readable English descriptions are local UI labels.
Several colors mean **any selected color**, combined with all other filters.
The API accepts repeated `color_hex=%23FFFFFF` parameters (maximum 32), matches
the stored metadata color exactly, and rejects malformed HEX values. No color
selection means all colors, including materials without a recorded color.

## Preview ordering

Material detail opens the preview gallery immediately below the title, selecting
FABRIC_1.png or SPHERE_1.png first when available. Subsequent previews follow
their numeric suffix, so `_2` precedes `_10`. Arrows cycle through the previews;
reload returns to the primary preview. Bounded image conversion and object URL
cleanup remain available.

## Selected offline publication preparation

Administrators and production leads use **Prepare selected for publication**
from either List or Gallery. The workspace freezes up to 100 explicitly checked
material IDs; it never silently truncates a larger selection. Materials has no
Prepare filtered action or Publication batches button. Leadership remains
read-only, and processors cannot create publication exports.

The workspace offers an optional automatic file check and **Review materials**,
which reports missing publication data and warnings. **Prepare publication**
opens the Windows destination folder picker. A successful export creates a new
uniquely named `REAWOTE-publication-...` subfolder there containing `materials.csv`,
the verified resolution ZIP archives and `export-receipt.json`. Existing files
are never replaced. Failed preparations are not exposed as a complete export.
ZIP methodology uses the global packaging cutoff/timezone settings.

This local workflow does not require the older content/technical approval UI.
Packaging still validates its source inputs and artifact integrity. It performs
no Google Storage upload; the user uploads the generated CSV and archives manually.

After generation, a dialog asks whether to mark the exported materials Published.
**Not now** leaves the flags unchanged. Confirmation is an audited manual decision,
separate from uploading files. The server checks the exported selection and current
source/data before changing any flag. Unknown outcomes retain the exact request
for recovery instead of silently creating another export or repeating a write.

## Meaning of the fields

| Field | Values / behavior |
| --- | --- |
| Status | In progress / Done; one workflow field |
| Checked | no / OK / Correction; explicitly human review |
| Automatic file check | not checked / OK (green) / issues (red); server-produced, read-only |
| Published | Yes / No; manual evidence, no upload or importer action |
| Archived | Yes / No; logical archive with a recorded archive date, ADMIN only |
| Note | Optional multiline text; independent of publication description |

A transition to Done still reads and verifies the linked source folder and
records its metadata snapshot. Missing metadata.txt remains nonblocking for a
safe folder. A new Done transition resets Checked to no. OK requires Done.
Correction reopens the material as In progress. Ordinary changes that invalidate
a source/content review also reset Checked; automatic technical validation never
grants human OK. Note and Published edits do not invalidate approvals.

The old validation/publication state columns remain internal pipeline data and
historical evidence. **Automatic file check** replaces the old File check label
and uses its own derived result, independent of manual Checked. Detail uses the same editable
property controls as the table. Source review, AI/service controls, content approval,
per-material ZIP policy and snapshot history panels are omitted; backend history
and legacy endpoints remain available.

Administrators and production leads can edit the ordinary properties; Archived is ADMIN only. Processors
can change Status and Note on their own assigned materials. Leadership is read-only.
Archived is also available for the fixed filtered selection in bulk changes. Each
row uses its own lifecycle preview and exact command; uncertain outcomes retain
that packet and stop the remaining rows until recovered. No reason entry is required.
Changing Archived preserves Status, Checked, Published and current metadata.
Every endpoint rechecks current authorization, archival state and active-operation
locks, independently of what the browser enables.

## Automatic file checks and reports

Administrators, production leads and assigned processors can run a check from
the material card or **Check selected materials** in Materials. A bulk check
freezes at most 100 selected IDs and their revisions; authorization and current
versions are rechecked before results are saved. Users cannot set check status,
timestamp or report through ordinary property updates.

The configured desktop uses the complete `PBR_FILES_V1` rules documented in
[Automatic file check](automatic-file-check.md). Completed clean checks produce
**OK**; defects produce **issues**. Historical or unconfigured `BASIC_V1` checks
remain preliminary and cannot certify OK. Checking does not change Status,
human Checked or Published. Relevant later material changes invalidate the
observation.

Checks run as background jobs and show completed/total materials, active files,
elapsed time and cached image evidence reuse. At most two materials are inspected
concurrently. A connection failure offers recovery of the same job without
automatically starting another scan. The bar reaches 100% only after the server
confirms completion and saving; incomplete checks cannot write a partial OK.

Bulk checks create a UTF-8 TXT report in
`%LOCALAPPDATA%\REAWOTE\Reports\Checks` and open it in Notepad. This persistent
per-user folder is outside the source library and survives routine temporary-file
cleanup. The UI shows the report path and retains a download option if desktop
saving or opening is unavailable. Filenames include a timestamp and unique ID.
Only defective materials have detail sections; passing materials are counted in
the summary.
The full report remains in the database, audit history and check result/TXT;
ordinary material lists and property-write receipts carry only its status,
timestamp, profile and completeness flag. Large reports do not block later edits.

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

Migration 0033 adds the separate automatic check status, timestamp, report,
profile and completeness flag. Existing records start as not checked without
changing their human Checked, workflow, Published or metadata. PostgreSQL limits
the derived status to the three documented values. The exact-response guard is
extended for new material write receipts; existing receipt JSON and hashes stay
unchanged. Downgrade refuses to discard recorded automatic check results or
receipt history containing the new fields.

The R100 local acceptance app still uses a dated derived-preview snapshot and
has no live NAS worker connection. Ordinary database cell edits work there;
Done preflight and linked identity planning require that connection. Source
mutations, packaging and external publication remain disabled in that instance.


## Archive view (2026-09-27)

Archive is the same Materials component with a fixed `is_archived=true` query,
including search, all filters, list/gallery, Properties and row selection. Archive
date is shown with the Archived checkbox. Clearing Archived restores the record.
Published is preserved. ADMIN can change Note, Project, Processor and Published;
workflow, Checked and identity need restore. The migration preserves old lifecycle
history, active-job fences and recorded external-dispatch blocks. See the
[archive contract](material-archive-contract.md) for state/recovery details.
