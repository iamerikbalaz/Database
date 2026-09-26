# Projects and companies as editable databases

The lists now support search, filters, visible properties, selection, inline editing
and reviewed bulk changes over the entire current filtered result. Projects filter
by company, status and presence of a NAS link; companies filter by activity and
country. Project search includes notes and the folder path.

Each row write captures the row's `updated_at` and a unique idempotency key. The
backend locks the row and rejects stale revisions. A batch captures IDs, versions,
values and save context before confirmation. Results are per row; an uncertain
network result pauses the batch and retries the identical request. Successful rows
are never resent. A session change or unmount prevents further queue execution.
Refresh reapplies filters after a saved property changes. Column visibility is a
local browser preference. Existing detail editors and API consumers remain
compatible; their PATCH precondition is optional, while all new table edits use it.

## NAS references and import

`Project.folder_path` is nullable and stores the existing folder reference. List
and detail expose its exact text and a copy action, since browsers cannot reliably
open Windows SMB paths from an HTTP page. Editing a project does not rename, create
or modify a NAS folder. Original paths and project numbers are preserved.

`app.project_import.plan_project_import` is pure read-only planning: it accepts
observed immediate child folders plus current API project records. The standard
name is `NNNN_MANUFACTURER_JOB-TYPE_MATERIAL-SPECIFIER_MMYYYY`. The optional legacy
mode accepts the four-part form without a material specifier, marks that value
`None`, and emits `LEGACY_MISSING_SPECIFIER`. Malformed names, invalid dates, paths
outside the chosen root and duplicate project numbers are quarantined. A matching
existing project number/path is an idempotent no-op; an unlinked existing number is
reported separately for review; conflicting paths never overwrite an existing
record. Import does not infer completion or a deadline from the folder month.
Historical materials remain unassigned unless the user explicitly assigns them.

The import operator creates or matches a company from observed manufacturer names
and supplies `company_id`, original `project_number`, original folder `name`, and
`folder_path` through the ordinary authenticated, receipted project API. Company
matching must be conservative and ambiguous matches left for review. Companies
created from manufacturer names are test directory entries, not verified legal
entities. Company address, tax and website data must not be invented.

## Migration and compatibility

Migration `20260926_0028` adds the folder reference and updates exact-snapshot audit
and command triggers. Legacy snapshots and receipts remain unchanged and readable.
A downgrade refuses to remove populated references or new-format project history.
The source directories are never accessed by the migration. Historical PostgreSQL
fixtures insert against their historical table shape before upgrading.

Focused tests cover pure parser/planning, duplicate/legacy handling, permissions,
optimistic conflicts, same-key recovery, history and folder references. Additional
PostgreSQL tests check old receipts/history and competing actors. Browser unit
tests verify frozen batches, uncertain recovery, stop behavior, draft preservation,
filtered project bulk edits and company inline revision payloads.
