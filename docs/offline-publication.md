# Offline publication export

The desktop Materials view prepares a selected set of at most 100 materials.
This workflow writes CSV and ZIP files for a user to upload manually. It never
connects to Google Storage, performs an upload, or marks Published implicitly.

## User flow

1. Select materials and open **Prepare selected for publication**.
2. Run **Review materials**. The optional automatic file check uses the separate
   preliminary check/report workflow; it is not a technical approval.
3. Resolve missing publication fields. Done, an active brand/catalog selection,
   credits, a color and sample dimensions are required. Empty descriptions and
   tags are warnings. Existing legacy technical/content approval records are
   not required by this offline workflow.
4. **Prepare publication** opens the native folder picker. Cancellation writes
   nothing. The application creates a unique `REAWOTE-publication-<date>-<id>`
   subfolder inside the selected destination containing `materials.csv`, all
   generated resolution ZIPs, and `export-receipt.json`.
5. After successful completion, explicitly choose whether to mark the exported
   materials Published. Declining preserves their current values. A later
   database or source-file change blocks the entire confirmation and requires
   a new export; no subset is marked silently.

The CSV retains the existing UTF-8 BOM, semicolon delimiter and columns:
`identity_name;name;description;credits;dimension;brand_identifier;categories;color;tags`.

## Existing ZIP methodology

The offline worker calls the existing `validate_material`,
`stage_packaging_inputs`, and `assemble_packages` implementation. It does not
introduce another ZIP format or image-resizing algorithm. This preserves master
selection, generated resolutions, map precision, preview copying, the generated
web manifest, and the separate production metadata inside each resolution.
SOURCE files remain outside publication archives.

The bounded map vocabulary includes the historical `DIFF`, `METAL`, `SPEC`,
`ID` and `MASK` maps observed in the test set. They use the unchanged historical
resize/precision rules and remain present in each ZIP and generated web manifest.

Global packaging settings select method A strictly before the configured cutoff
and method B from the cutoff onward in the configured storage timezone. The
first recorded original master modification timestamp is used when available;
an explicitly injected, immutable `original_master_observations` map can provide
trusted copy/import provenance by material UUID where no original policy record
exists. This value participates in the frozen preview hash. The local Test_data
runtime uses the original copy inventory: renaming a material can change its
current directory timestamp and must not change its historical ZIP methodology.
New production imports must capture original timestamps before modifications;
creation/import dates must not be substituted. Without either provenance source,
the source master directory timestamp is used. Method A normalizes
ZIP timestamps to 1 January 2026; method B retains packaging timestamps.

## Runtime and access

`create_app(local_publication=...)` explicitly injects a
`LocalPublicationExport`. Ordinary deployments leave this capability disabled.
The adapter receives an existing `LocalMaterialLibrary`, a separate private job
directory, a fixed local Docker executable/context, and an immutable image ID
built from `worker/Dockerfile.packaging` (runtime target).

The one-shot container has no network, a read-only root and source mount,
dropped capabilities, and bounded private work storage. Native Windows handles
keep existing source files stable while packaging. The worker generates and
verifies archives using the existing ImageMagick runtime; it only writes its
explicit private output mount.

All HTTP routes require authenticated ADMIN/PRODUCTION_LEAD access, loopback
origin and normal CSRF protection for POST requests. No HTTP request accepts a
destination path: the picker issues an opaque, actor-bound, single-use capability
valid for 15 minutes. Source, source-journal and export-job directory overlaps
are refused. Destination directory identity is rechecked before copying.

Exports use durable UUID receipts and frozen database, metadata and packaging
settings. Exact retries return the original receipt. Only one export runs at a
time. Completion rechecks current access and material values under database
locks during the final atomic directory rename. A failed export can leave an
explicit `REAWOTE-incomplete-<id>` folder, never a complete publication folder.
Private job artifacts are retained for diagnosis; there is no automatic cleanup
policy yet. An interrupted server process exposes a failed receipt after restart
and does not silently retry filesystem output.

Published confirmation rehashes the current complete source file trees against
the exported inventory, then atomically checks the frozen database/content/
metadata/settings and applies the Published flags. Resource history records each
change with its actor, and a keyed audit receipt makes confirmation replayable.

## Routes

- `POST /api/local-publication/preview`: frozen selected IDs, field review and
  preview hash.
- `POST /api/local-publication/destination`: native picker and opaque capability.
- `POST /api/local-publication/exports`: selected IDs, expected preview hash,
  destination capability and UUID idempotency key; returns a job receipt.
- `GET /api/local-publication/exports/{id}`: actor-scoped running/completed/failed
  state, destination, CSV name and archive hashes/sizes.
- `POST /api/local-publication/exports/{id}/published`: explicit all-or-none
  confirmation with its own UUID idempotency key.

Validation covers authenticated API boundaries, stale selections, publication
atomicity/replays, real Windows destination/source-handle behavior and Linux ZIP
assembly with both global date methods.
