# Material drafts, colored states and administrator deletion

## User-visible behavior

- Material properties use two desktop columns: selects and Note on the left;
  Published and Archived at the top of the right column, followed by automatic
  file check and read-only values. Narrow screens stack the columns.
- Materials Status and Checked have readable colored values in rows, cards,
  filters and bulk editing. Orders Status/Priority use the named option colors
  read from the Notion Orders schema on 2026-10-05. Native select menus retain
  OS keyboard behavior; operating systems may override menu highlight colors.
- Materials and Orders have a small folder button at the beginning of each row.
  Missing folder references disable it. It does not select/highlight the row.
  The authenticated local desktop endpoint opens a verified configured folder
  through Explorer; a caller cannot supply an arbitrary executable or path.
- Add material requires only one name or a pasted column of names. Missing
  Customer, Order, Main category, Processor and template remain genuinely absent.
  A draft has no allocated number, generated identity, linked folder or Published
  state. Supplying Customer and Main category later reserves a real customer
  number and creates the identity atomically. A separate Create material folder
  action then uses that record, with an optional SBS template, without another
  number or duplicate record. Complete creation with Customer/category continues
  creating its folder directly. No XK folder is generated.
- Draft Edit Name edits the database record with the existing durable ordinary
  write protocol. Linked-material renaming retains the reviewed filesystem flow.

## Deletion contract

The trash action appears only for ADMIN, for explicit selections of 1–100
materials in List, Gallery or Archived materials. The backend enforces the same
role for planning, applying, reading and resuming deletions.

The dialog freezes the selected records, reviews their versions and folders,
and requires confirmation of one of two modes:

1. **Records only:** remove records from every active/archive view; source files
   remain in place.
2. **Records and source folders:** atomically move each entire source folder out
   of the library into the desktop adapter's protected recovery journal on the
   same volume. Files are retained, not irreversibly purged. The dialog explicitly
   explains this behavior. Published library copies and uploads are unaffected.

This is separate from reversible Archived. Deletion retains an immutable
database tombstone, author/audit evidence, number and identity/path reservations.
Direct material URLs and cached source operations recheck visibility; deleting
does not allow reimporting the same reserved identity as a new record.

The source mode rejects missing/shared/overlapping folders and any folder whose
basename is not the material's exact recorded identity. Native filesystem handles
prevent symlink/reparse substitution and pin the authorized directory identity.
Recovery recognizes an already-moved original and never moves a replacement
folder created at its old path. There is no automatic permanent purge.

Deletion authorization and ownership commit before source IO. Active creation,
identity, metadata, preview, packaging/staging or customer rename operations
block conflicting changes. Exact request keys, revision hashes and receipts
allow retries after a lost response; a later 403 never proves the first write
failed. Partial/recovery outcomes stay visible and can be resumed by an active
administrator after reviewing the complete recorded selection. Authority is
rechecked after IO, before completing the database write.

## Schema and compatibility

- `20261005_0043`: nullable draft identity facts and creation-batch customer,
  guarded by a database invariant; complete records retain required identity.
- `20261005_0044`: deletion timestamps, immutable receipts and active ownership.
- PostgreSQL's ordinary write receipt validator includes the public `is_draft`
  field and excludes internal `deleted_at`. Old receipt JSON remains unchanged;
  the frontend accepts its absent draft field without manufacturing a new key.
- Historical customer renaming skips incomplete drafts. Their eventual identity
  uses the customer's then-current prefix. Collision checks explicitly include
  deleted historical reservations even though ordinary database views hide them.
- Downgrade refuses to lose drafts, newly shaped receipts, deletion history or
  recovery ownership. After new writes, use a forward fix or an explicitly
  approved backup restore rather than discarding those records.

## Verification

The isolated rehearsal starts from the desktop test database at 0042. The final
0042→0044 upgrade preserves all existing table counts and passes Alembic schema
comparison. PostgreSQL tests exercise actual concurrent allocation/deletion,
immutable audit triggers and interrupted-operation recovery. Synthetic Windows
fixtures exercise folder creation and whole-folder quarantine; no user material
folder is renamed or deleted by acceptance testing.

Frontend: the full suite passed 1,305 tests, lint and production build. The final
draft rename addition subsequently passed 34 focused tests, lint and build.
Backend: deletion/import final suite 59 tests, broader affected regression 243,
creation/folder final suite 38, and root schema/metadata/desktop suite 59 passed
(these suites overlap; totals must not be added). Customer rename tests cover
both historical rename choices with incomplete drafts. Final PostgreSQL and live
deployment results are recorded in the checkpoint. The final selected PostgreSQL
regression covered 53 cases: 51 initially passed, then the two remaining tests
passed after updating an expected downgrade guard and isolating a synthetic
authentication fixture race. A further test covered allocation by two distinct
administrators. No allocator workaround or weakened assertion was needed.

Live acceptance passed 31 existing endpoint/page checks and 10 new API/asset
checks. The running test is on schema 0044, serving the built files byte-for-byte;
all 51 material records and versions were retained. The upgrade also verified
every prior material field, not just the record count, against its fresh backup.

Unrelated full backend suites were stopped before completion; they are not
reported as passing. Browser rendering and physical mouse interaction were not
exercised in this iteration; UI checks use component behavior and source layout.
