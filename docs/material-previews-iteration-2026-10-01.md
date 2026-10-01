# Compact material workspace and source preview editing

## User-visible behavior

- Keep filters sits above Clear filters, aligned with the other filter labels.
  Sort is separate below the filter group. Defaults: Customers/Catalog A–Z,
  Materials newest first, Orders highest number first. Explicit saved preferences
  remain respected. Materials also offers both Number directions.
- Number, when enabled, follows Preview. The Preview header arrow expands every
  row to a left-aligned strip of all PNG previews, with full filenames below.
  Thumbnails reuse the bounded gallery cache and are requested only near the
  visible area, including horizontally offscreen images.
- Double-click a filename (or focus it and press Enter) to rename its source PNG.
  The trash button opens a confirmed deletion dialog. Edit previews in the bulk
  panel supports literal replacement and deletion by substring, with actual
  file counts and per-material plans before confirmation. Matching is case
  sensitive, excludes the `.png` extension, and deletion uses original names and
  takes precedence over replacement. Empty selectors do nothing.
- Categories / collections, Auto-check, publication preparation and Edit previews
  are grouped with bulk changes and appear only when materials are selected.
  Check progress and completed reports survive list refreshes.
- Main category is a bulk property. Linked materials use the existing identity
  workflow to rename folders/files and update metadata. They must be unpublished
  and In progress; ineligible/stale rows are listed, never bypassed. The name
  pencil opens the same confirmed material rename dialog as the detail page.
- Material detail puts a smaller preview left of compact editable properties.
  Categories and collections are collapsible multicolumn groups. Add material
  puts common parameters left and single/multiple material names right.
- New material creation makes PREVIEW and SOURCE plus the chosen SBS template.
  It no longer accepts a resolution from the UI or creates XK texture folders.
  Previously recorded creation batches retain their frozen recovery structure.

## Preview edit guarantees

Migration `20261001_0042` adds durable preview edit receipts and material/source
ownership. Operations require authenticated editor access, material assignment
where applicable, CSRF, local desktop capability and the explicit source-mutation
switch. Planning is read-only. Confirmation checks the exact source fingerprints
and material timestamps and records authorization before touching files.

File edits cover only direct PNG-named files inside a linked PREVIEW folder.
They use the existing protected Windows handles and no-overwrite renames.
Traversal, junctions, hard links, colliding names and changed sources are rejected.
Temporary staging handles case-only renames and interrupted operations. Deleted
bytes remain in the private journal quarantine; no automatic purge or GUI restore
is added in this iteration. A corrupt PNG can still be renamed/deleted; file
content validation belongs to Automatic file check.

Changes reset Checked and Automatic file check and mark affected materials
unpublished. The material history records the actor and file-change result.
Unknown replies retain the same request key; unfinished operations can be found
by selected material IDs after reload and resumed. Limits remain 100 materials,
64 PNG files per material, 64 MiB per PNG and 512 MiB per material.

## Verification

- Full frontend: 95 files / 1,205 tests passed; lint and production build passed.
- Creation/layout: 31 focused backend tests, including native Windows creation
  without XK and recovery of an older frozen XK batch.
- Preview editing: 28 API/native Windows tests, including actual authenticated
  API-to-filesystem changes in isolated temporary folders, collisions, source
  changes, two crash points, corrupted PNG quarantine and replay.
- PostgreSQL: 4 isolated tests cover upgrade/data preservation, schema comparison,
  constraints, immutable receipts, ownership, audit and interrupted recovery.
- Shared backend guards: 112 migration-head, metadata, identity, material-table
  and source-metadata regression tests passed.
- Component regressions cover preview expansion, Number placement, selection-only
  actions, source-operation confirmation, ambiguous replies and retained reports.

Live source writes stay in the Desktop Test_data capability root. Verification of
the deployed application uses read-only real preview planning; it does not rename
or delete a user's preview. Runtime logs, fresh database backups and acceptance
results are kept in the ignored `tmp/iteration-20261001` directory.

Deployment to the owned local instance at `http://127.0.0.1:53033` completed on
schema 0042 after a fresh custom-format PostgreSQL backup. Schema comparison was
clean and record counts remained 51 materials, 310 internal customer/brand
records, 283 orders and 15 internal users. Live API/asset acceptance and a real
preview rename plan passed; the selected source SHA-256 was unchanged and no
preview operation was applied to live data during verification.
