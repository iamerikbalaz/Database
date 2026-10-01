# Database editing, material creation and offline publication — 2026-10-01

## User-visible changes

- Materials, archived Materials, Customers, Orders and Catalog share name/date
  sorting and opt-in **Keep filters**. Preferences are scoped to the authenticated
  user and database in this browser. They persist after sign-out/sign-in, but are
  not synchronized between different devices or browsers.
- Color filters open above the table's horizontal scroll area, use the common
  chevron, and Clear filters aligns to the right of the filter panel.
- Catalog names and abbreviations change in the **Edit category** dialog. The
  warning recommends deactivating/replacing a category; spelling corrections are
  allowed. Existing material identities and folders stay unchanged. Main-category
  choices use the live vocabulary; abbreviations must be unique. Historical
  abbreviations remain reserved for the same category, so renaming its label/code
  keeps existing material associations and filters without renaming their folders.
  Catalog has no
  bulk-edit action, while Active can still change inline.
- Add order suggests the next four-digit number from both database and configured
  order folders and today's Prague date. Users can change both. Orders can be
  sorted by number; the first column is NUMBER. Order details show material
  previews, Status, Checked, Automatic check, Done counts and progress, including
  the empty-order case.
- Materials may have no Order. Selecting an Order fills and constrains Customer;
  server-side checks also enforce the relationship for subsequent edits. Existing
  historical inconsistencies are not silently rewritten.
- Add material supports one material or pasted names from one Excel column,
  shared Customer/optional Order/Processor/Main category, additional categories,
  brand collections, integer 1–32K resolution and an SBS template (up to 64 MiB).
  A batch of up to 100 names creates
  distinct uppercase identities and customer/material folders containing
  `{K}K`, `PREVIEW` and `SOURCE`, with the chosen template copied into SOURCE.
  Existing paths are never overwritten. Partial batches retain their reserved
  identities and can be resumed with the same request key.
- **Add categories / collections** adds selections to multiple materials.
  Collections are available only when all selected materials have the same
  Customer. Existing assignments and each material's main category remain.
- Settings **Paths** stores SBS templates, new Orders and new Materials roots.
  Existing material paths are never moved when these settings change. During
  testing, new material data is restricted to Test_data and its subdirectories.
- Customers has Published, Country, published filtering/bulk edit and the separate
  five-column brand CSV export. Customer status labels are Active cooperation,
  Test sample, and In library (not verified).

## Live test and publication

The owned test application remains at `http://127.0.0.1:53033`, running schema 0041.
The deployment preserved 51 material records, 310 internal brand/customer records,
283 Orders and 15 internal-user records. The internal brand count includes legacy
records that are not shown as Customers. No existing material folder was moved.
New Orders can create folders under `R:\0. PROJECTS`.

Default paths:

- SBS: `C:\Users\Admin\Desktop\Substance graphy vzory` (15 templates found).
- Orders: `R:\0. PROJECTS`.
- Materials: `C:\Users\Admin\Desktop\Test_data`.

The publication runtime was rebuilt from the current worker. Original scripts
and import templates were inspected. ZIP output keeps original 1024-per-K rules,
aspect ratio, image format/precision, PREVIEW, metadata and global A/B date policy.
SOURCE is excluded. Brand collections are omitted from material CSV. Upload to
Google Storage remains manual; Published is only set after explicit confirmation.
See [offline publication](offline-publication.md) for the complete contract.

A real 4K Test_data material was exported from a disposable database clone with
an explicitly synthetic publication draft. Its 4096/2048/1024 ZIPs each contained
nine maps and two previews. CRC, SHA256, dimensions, manifest, CSV BOM/header and
method-A timestamps were checked. All source hashes stayed unchanged; the QA
export did not mark anything Published or modify live material data. The output
is under an ignored `QA_ONLY_DO_NOT_UPLOAD-*` directory. This acceptance does not
claim a real 8K/16K run; larger-source runtime/memory behavior still needs testing.

The existing live sample materials currently need publication credits and saved
library drafts before their real publication exports can succeed. The preview
reports these missing inputs; acceptance did not invent live commercial content.

## Notion and public brands

Published was added to Notion Customers. Public REAWOTE Brands pages supplied 149
brand entries; 144 unambiguous matches were marked Published in both the app and
Notion (124 exact normalized names plus 20 verified Brand identifiers). Ambiguous
or absent matches were left unchanged. In particular, online Zero and ZERO-LACK
have conflicting identifiers and were not automatically linked.

The three status labels are migrated in the application. Renaming the existing
Notion Status options remains a manual Notion change: the connector only exposes
the simple STATUS type, and the official API does not rename existing options.
Do not replace the property or discard its existing options to simulate a rename.
Required changes are Active → Active cooperation; test → Test sample;
In library → In library (not verified).

Ongoing one-way application-to-Notion delivery remains disabled until a dedicated
application integration token is configured. The connector used for the authorized
one-time Published update is not an application credential. The outbound code now
supports Published and the new status labels, including old queued payloads.

## Migration and recovery

Migrations 0037–0041 add customer publication/country, versioned Paths settings,
durable material creation batches, audited catalog identity edits, and reserved
historical category codes. They were
first applied to a fresh clone, then to the owned test instance. Alembic reported
no schema drift. A fresh private database dump and pre-deployment runtime settings
were retained in `tmp/iteration-20261001/` before upgrading the live test.

Downgrade must not discard populated Published/Country or creation history. For
rollback, first preserve all newer user data, then restore the owned test backup
with its matching previous code/runtime; do not point old code at the new schema.
Never restore a database over active filesystem operations. No main-branch merge
or production-library rewrite is part of this iteration.

## Validation

- Complete frontend: 91 files / 1,182 tests passed; lint, TypeScript and production
  build passed. The existing bundle-size advisory remains non-blocking.
- Complete Linux/ImageMagick packaging suite: 1,056 tests passed, plus the real
  read-only-source ZIP/CSV acceptance described above.
- Broad backend run: 2,645 passed, 499 environment-specific skips, nine outdated
  expectations failed (old migration head, mandatory Order, old rename contract).
  Those expectations were corrected and their suites rerun successfully: 53
  content/AI tests, 92 material/name-guard tests, and the final 68-test catalog,
  creation, table and migration-head regression set. Additional creation/NTFS,
  directory, Paths, brand CSV and outbound tests passed during implementation.
- Broad PostgreSQL run: 496 passed and two historical-migration regressions found.
  Both were repaired and covered by a passing nine-test rerun. Four further
  PostgreSQL alias/identity/downgrade regressions passed for schema 0041.
- Fresh clone and live test-instance migrations reported no Alembic drift.
  Final live API checks passed for Customers, Orders/defaults, Paths/templates,
  material creation options, catalog aliases, brand CSV and frontend assets.
- Fresh browser interaction/visual verification remains unavailable under the
  existing automation restriction; GUI behavior was checked with component tests.
