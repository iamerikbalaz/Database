# Historical PBR import plan

Current slice after completed gallery verification. Handoff requirements establish historical
Excel/NAS import and prohibit guessing project/company or online-asset mappings.
No actual historical workbook or production NAS corpus is available in this task.
Implement and verify against synthetic inputs, without claiming a live migration.

## Conservative contract

- Imports create PBR material records only. They never silently update an existing
  UUID, relink a folder, change an identity, grant approval or mark an online asset
  published. Conflicts require an explicit decision outside the import.
- Every row maps explicitly to existing project, published brand and processor
  IDs. The project company and brand company may differ. No company is inferred
  from a project name or spreadsheet label; no resource is created implicitly.
- Preserve a historical four-digit identity only when its exact brand prefix and
  category validate. Check the permanent number ledger, existing materials and
  duplicate rows. Never reuse an allocated number. Lock affected brands in stable
  order and advance each counter to at least the highest imported number plus one.
- Preview the normalized rows and blocking findings before confirmation. Bind the
  confirmed input and referenced database state to a hash; reject stale plans.
  Confirmation is all-or-nothing, actor-scoped and idempotent. Record an immutable
  batch/row audit including source digest and explicit mappings.
- Limit input bytes, row/column/cell counts and decoded workbook expansion. Reject
  formulas, macros, external links and unsafe workbook structures; do not execute
  spreadsheet content. Show safe row/column findings without logging cell values.
- Treat claimed historical workflow/publication information as unverified input.
  Imported materials begin in progress and require the normal source/review flow.
  Do not fabricate metadata snapshots, approvals or online import acknowledgments.

## Small delivery steps

1. Bounded CSV/XLSX reader and explicit column mapping contract, pure validation
   tests and representative synthetic spreadsheet fixtures.
2. New migration 0012 for immutable import audit; authenticated preview/confirm
   API with number allocation, conflicts, idempotency, actual PostgreSQL races,
   fresh/prior-0011 upgrades and downgrade checks. Prior migrations stay immutable.
3. UI file/mapping/preview/confirmation flow with unknown-outcome replay, errors
   and real retained-data E2E. No automatic writes merely from selecting a file.
4. Read-only NAS discovery and explicit folder matching through the secure worker;
   reuse source preflight/link validation and keep all source files untouched.
   Discovery must be bounded and must not follow links or scan arbitrary paths.

The details of unknown historical column names are handled by explicit mapping,
not hard-coded guesses. AI generation/provenance, publication CSV, packaging and
GCS/Notion adapters remain separate work. Existing online-asset reconciliation
remains blocked until its importer contract is supplied.

## Source reader contract

The pure CSV and XLSX readers and administrator-only `/api/material-imports/inspect`
endpoint are implemented before database mutation. They return immutable source rows and an original-file
SHA-256. Errors contain fixed codes and row/column coordinates, without cell data.
No files are extracted, formulas executed, external links resolved, or source
files rewritten. Parser inputs and table values are excluded from object reprs.

- UTF-8 CSV with optional BOM; comma or semicolon must be selected explicitly.
  Quoted delimiters, quotes and multiline values are supported. The first record
  is the header; subsequent blank records are counted but omitted from output.
- XLSX uses an explicitly selected sheet. Its first stored row is the header.
  Shared/inline strings, rich text, stored numbers, booleans and ISO date strings
  are read as literal values. Formatting and numeric date/zero-padding formats
  are not interpreted. Hidden rows are included. Legacy XLS is unsupported.
- Maximum file 4 MiB, 2,000 data records, 32 columns, 2,048 characters per cell.
  Headers must be nonempty, single-line and unique after NFC/case folding.
  XLSX row coordinates must also remain within rows 1–2,001.
- ZIP limits: 128 entries, 8 MiB per expanded entry, 16 MiB total expansion.
  XML limits: 250,000 elements across the package, depth 64, 32 attributes per
  element. DTDs/entities, macros, embedded objects, connections, external
  relationships, ambiguous archive paths and merged selected-sheet cells fail.
- Formula-bearing XML is rejected across all parsed parts, including unselected
  sheets. Literal cells beginning with `=`, `+`, `-` or `@` after whitespace are
  conservatively rejected in both formats. This includes negative literal values;
  this material-identity import is not a general numeric spreadsheet importer.
- `defusedxml` is a pinned runtime dependency. `openpyxl` is a test-only writer;
  compatibility tests create actual styled, sparse and multi-sheet workbooks
  independently of the reader, including real formula/link rejection cases.

Implementation references: [Microsoft SpreadsheetML structure](https://learn.microsoft.com/en-us/office/open-xml/spreadsheet/structure-of-a-spreadsheetml-document),
[stored cell values](https://learn.microsoft.com/en-us/office/open-xml/spreadsheet/how-to-retrieve-the-values-of-cells-in-a-spreadsheet),
and [defusedxml parser controls](https://github.com/tiran/defusedxml).

The inspection API requires an actual active administrator session, completed
password change, CSRF and allowed Origin. The JSON envelope is bounded at 6 MiB
(4 MiB source encoded as base64 plus mappings), with a ten-second upload deadline
and two in-flight imports per API process, held through parsing. Unsupported
content encodings, duplicate JSON properties and non-finite values are rejected.
Authorization is checked before accepting the upload and after parsing, including
failed parsing. Errors do not reflect source cells or arbitrary request keys.

Choose five distinct existing headers: identity, name, project, brand and processor.
Inspection returns at most ten sample rows and at most 64 distinct labels for each
reference group. Labels normalize NFC and outer whitespace; blank or multiline/
control-bearing reference labels require source correction. Map each literal label
to an existing UUID; do not infer IDs from names or prefixes. Identity/name validation
and duplicate brand-number detection are implemented independently of database IO.

The database-backed preview/confirmation and immutable audit are now implemented
and verified on actual PostgreSQL. See [the backend contract](historical-import.md).
Next: complete the import UI and fresh/retained browser verification. The
production lead's ordinary material-create permission does not implicitly grant
historical number import. No workbook headers or company mappings are guessed.
