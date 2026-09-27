# Material workflow simplification — 2026-09-27

## Interface

- Archived is a checkbox in the list and detail, and a bulk property. Archive
  uses the same Materials table/gallery, filters and Properties disclosure with
  `archived=true`. A date identifies the latest archive. Restore clears the flag.
- Archive/restore preserve Status, Checked, Published and current metadata.
  Internal technical review becomes stale; active source/packaging/publication
  operations still block conflicting changes. Old audit records remain intact.
- Detail shares the list's property controls. Identity changes for linked folders
  still use a controlled rename plan. A collapsed general property history remains;
  metadata snapshot history is hidden.
- Source inventory/review, AI proposals/service settings, Content approval and
  per-material ZIP policy are omitted from the material detail interface.
- The Properties disclosure and selection header follow the other databases.
  There are no separate Select all filtered/Clear selection buttons.

## Source data

`Folder contents` lazily lists one directory level, including file sizes, with
navigation restricted to the material's linked folder. It does not load textures
or hash entire trees. The worker retains descriptor-based path checks, entry/time
bounds, source-change checks and post-IO authorization.

The [metadata editor](editable-source-metadata.md) updates or creates root
`metadata.txt` with explicit writes enabled. It preserves unknown fields,
checks the observed source hash and safely recovers an uncertain save.

The [historical importer](historical-import.md) includes color, size in cm,
Done, Checked, Note and brand identifier. Existing source folder names and paths
are preserved. Importing spreadsheet metadata performs no filesystem write.

## Companies and packaging

Company creation/rename and brand rename/transfer maintain a same-name company
brand. Existing matching brands keep their identifiers. Newly generated identifiers
are provisional database identifiers; creation does not create or rename a NAS
folder. Notion company-name adoption uses the same transaction rule.

ZIP method A/B follows the global cutoff and timezone in Settings. New batches
freeze that settings revision and derive each material's method automatically;
existing accepted packages and receipts retain their original policy. Phase-one
publication performs technical checks and does not require a separate human
Content approval. See [packaging policy](packaging-policy.md).

## Local R100 acceptance boundary

The owned acceptance database was backed up before migrations 0030–0032; all
existing rows were compared before/after migration. Spreadsheet enrichment
preserves changed user fields and the existing archive flag.

Docker Desktop cannot bind this host's mapped R share. A private Windows
read-only adapter therefore lists only the 100 selected source trees, holds
directory handles against rename, rejects reparse points and bounds/rechecks
listings. All 100 folders and 280 preview filenames were verified without file
content reads or source writes. Previews retain the existing dated local cache.

Technical checks and metadata writes to R still require a properly connected
worker. The acceptance worker reads an owned synthetic Windows source tree and
can write only the exact metadata-test material mounted from an owned Linux
volume. The verified metadata fixture exercises creation/editing and replay;
`#zip-test` exercises ZIP/CSV. An earlier Windows-bind metadata fixture retains
its pending operation and journal for diagnosis; the private guide links directly
to the verified Linux fixture. Neither worker mounts the original Y library.
External upload and Notion integration remain disabled for this instance.
