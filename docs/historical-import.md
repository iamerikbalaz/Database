# Historical PBR import

The `/imports` page includes standalone AI results review for administrators and
production leads above the administrator-only historical CSV/XLSX workflow.
The historical import API provides inspection, preview,
atomic confirmation and immutable batch history. Synthetic CSV/XLSX sources are
tested, including actual browser uploads and a restart with retained data. This
is not evidence of a production migration. A separate 100-row real-data catalog
acceptance is documented in [the R-drive test](historical-r100-acceptance.md).

## Browser workflow

Download the CSV template, replace its example row, then choose a file and its
explicit CSV delimiter or XLSX worksheet. The template is UTF-8 with a BOM and
uses semicolons. Select source columns and map each literal label to an existing record. Review the
preview, provide a reason and acknowledge that the imported records still need
normal source checks and approval. Any source or mapping change invalidates the
preview. Tables show Orders and Customers without legacy company nesting and paginate
large batches. Batch history is also paginated and links to current materials.

An unknown confirmation outcome freezes the exact request and its original
idempotency key for retry. A later rejected retry, including a 403, retains this
unknown state because the original request may already have committed.
The UI validates the returned batch against that
preview before reporting success. A definite rejected request requires another
preview. Source bytes remain in page memory; leaving or reloading the page loses
that local retry state. Check saved batch history before starting a replacement
import after such a navigation.

## Source and mapping

See [source limits and unsupported workbook structures](historical-import-plan.md).
Choose identity, name, Customer and Processor columns explicitly. An Order column
is optional; omit it and supply an empty order mapping, or leave individual Order
cells blank to preserve unassigned rows. An Order can subsequently be assigned
through the ordinary audited material edit. Every selected column must have a
distinct header. Map each literal Order/Customer/Processor label to an existing UUID. No resources, companies,
folders or online assets are inferred.
The selected Customer must be active and identified as a Customer; the Processor
must be active and have the PROCESSOR role. An Order's explicitly assigned Customer
must match the material Customer. Legacy Orders without a Customer remain usable,
matching the ordinary assignment rule. Internal legacy company records must still
be active, but they do not establish the Order/Customer relationship. Order status
is not imported and does not itself prevent import into an existing Order.

### Spreadsheet properties (2026-09-27)

Optional explicit mappings now include `color`, `sample_size`, `done`, `checked`,
`note` and `brand_identifier`. HEX accepts six digits with an optional `#` and
is stored uppercase. Sample sizes use centimetres: `10x20-cm`, `10x20`, decimal
commas/points and the multiplication sign are accepted. Explicit other units,
nonpositive values and excess precision are blocked for review, never guessed.

Done accepts YES/NO (case insensitive), true/false and 1/0; blank means In progress.
Checked accepts YES/OK, NO or Correction. OK requires Done; Correction cannot
remain Done. Imported Done/Checked are historical user values: technical validation
stays NOT_CHECKED and no approval record or source inspection is manufactured.
Color and dimensions append an internal WARNING snapshot with spreadsheet
provenance and no source-file proof. Automatic file-check status remains NOT_CHECKED,
with no report, timestamp or completed check. No metadata file is created or edited.

All nonblank identifiers for one Customer must agree; blank leaves the identifier
unchanged. Another Customer's identifier cannot be reused, including legacy aliases.
Confirmation atomically updates both the Customer-visible Brand identifier and
its legacy alias, with local Customer history and review invalidation. An actual
Customer change also records the normal transactional Notion sync outbox entry;
confirmation makes no external call. Unchanged values, legacy-alias-only repairs
and exact retries do not enqueue another Customer update. Preview shows these values before
confirmation. Schema-2 receipts store the mapped properties; existing schema-1
receipt hashes and exact replays remain compatible.

The exact identity uses `PREFIX_0001_MATERIAL-NAME_CATEGORY`: its selected brand's
prefix, a four-digit number 0001–9999, the original name component and the
canonical uppercase category suffix. Persisted three-part identities remain
readable. Imports preserve the exact source spelling; new identities normalize
spaces to hyphens and include the material name. Prefixes may contain
underscores but cannot contain path separators, colons or control characters.
For a named identity, the complete middle product segment is authoritative for
the material name and is stored uppercase: `ROUBAL_0001_TILES-ORANGE_B01` yields
`TILES-ORANGE`, even if the spreadsheet Name cell contains only `ORANGE`.
Hyphens and numeric product segments such as `20-08` are preserved. The mapped
Name cell remains the fallback for legacy three-part identities and is normalized
to the same uppercase product token (for example `Blue stone` becomes `BLUE-STONE`). New ordinary
materials and unlinked name edits store the same canonical uppercase product
name that appears in their generated identity; renaming a linked source still
requires a controlled identity operation.
The number must be absent from existing materials and the permanent reservation
ledger. Duplicate brand numbers inside a file and active brand identity operations
block the whole batch. Unused columns are identified but their values are ignored.

Imported materials receive new UUIDs, preserve their historical identities and
start IN_PROGRESS / NOT_CHECKED / NOT_PUBLISHED, with empty NOT_SCANNED metadata,
unless explicitly mapped historical Done/Checked or metadata values override the
corresponding fields as described above. Validation and publication remain unchanged.
An optional `folder` column records an existing path relative to the configured
library root; a blank cell leaves the reference empty. The final component must exactly match the identity. Absolute paths,
traversal, overlapping catalog references and active source owners are rejected.
The import does not read, create, rename or modify any source file, and therefore
marks these references explicitly unverified. Missing metadata files do not block
catalog import. Source inventory and approval remain separate operations.
Numbers below a brand's counter may be imported only if never reserved or used.
Each counter advances to max(current, highest imported number + 1); 9999 exhausts
ordinary allocation at 10000. Counters never move backwards.

## Supported fields and limits (2026-10-05 audit)

| Field | Import behavior |
| --- | --- |
| Identity, Name, category | Exact historical identity retained; full named product or normalized legacy Name; category read from identity suffix. |
| Customer, Processor, optional Order | Explicit links to existing records; no resource creation or inferred matching. |
| Folder | Optional relative reference only; never read or written by import. |
| Color, Sample size | Internal unverified metadata, six HEX digits and centimetres. |
| Done, Checked, Note | Explicit historical values; checked `no`, `OK`, or `Correction`; no automatic check or approval. |
| Brand identifier | Customer-visible identifier and legacy alias, with collisions checked and local history. Blank leaves unchanged. |
| Description, tags, citations | Use AI results JSON review above CSV for existing materials. CSV/XLSX ignores unmapped columns. |
| Credits, collections, publication, approval, automatic file checks | Not imported by this workflow. |

CSV/XLSX sources remain bounded to 4 MiB, 2,000 records, 32 columns and 2,048
characters per cell, with at most 64 distinct labels per reference group. UTF-8
CSV delimiter and XLSX worksheet are explicit. Formula cells, formula-prefixed
CSV values, macros, external links and unsupported workbook structures are rejected.
The downloadable example is synthetic: replace it before mapping and confirmation.

The October audit adds regression coverage for mixed blank Orders/folders,
Order/Customer conflicts after preview, customer identifiers/aliases, complete
uppercase names, actual template compatibility, property receipt comparison,
standalone AI access, and unchanged unverified technical state. Existing tests
continue to cover authorization, atomic rollback, receipts, duplicates and
permanently reserved numbers. No production import or filesystem operation was
performed for this audit.

## API

The internal mapping keys `project`, `brand`, `projects`, `brands` and
`published_brand_id` are retained for API/receipt compatibility; the interface
uses the unified Orders and Customers directory. Preview hashes also bind the
explicit Order/Customer relationship and Customer-visible identifier.

All routes require an active ADMIN session with completed password change.
POST routes also require the existing CSRF and allowed-Origin contract. API
responses use no-store. The upload envelope is bounded before JSON/source parsing.

- `POST /api/material-imports/inspect`: source `{format, data, delimiter?, sheet?}`;
  data is base64 of the original file. Optional `columns` returns distinct mapping
  labels. XLSX first returns sheet choices until one is explicitly selected.
- `POST /api/material-imports/preview`: source, columns and links
  `{projects: {label: UUID}, brands: {label: UUID}, processors: {label: UUID}}`.
  Returns normalized rows, source digest, current referenced records, ignored
  headers, fixed blocking findings and a confirmation hash only when valid.
- `POST /api/material-imports/confirm`: the same input plus an actor-scoped UUID
  `idempotency_key`, `expected_preview_hash`, boolean `acknowledge_unverified: true`
  and a nonempty reason. The server recomputes the preview in a fresh authorized
  transaction. Changed source, mapping, references or counter rejects the plan.
- `GET /api/material-imports?limit=20&after=UUID`: immutable batch summaries,
  descending by database creation time and UUID; maximum page size 50.
- `GET /api/material-imports/{batch_id}`: original batch context and created-row
  snapshots, even if materials have subsequently changed.

Confirmation takes the exclusive application access gate, the folder catalog lock
when importing references, and locks affected brands
in UUID order. Materials, metadata, reservations, counters and audit are committed
together. No filesystem or external service is involved. Retrying the identical
actor/key/payload returns the original result, including after later material edits.
Changing the payload for a previously used key returns a conflict. A lost response
must be retried using the original input and key, not a new key or a partial batch.

## Audit and rollback

Migration `20260917_0012` adds `material_import_batches` and `material_import_rows`.
Migration `20260925_0026` makes the existing material project foreign key nullable;
all earlier migrations remain unchanged. Its downgrade refuses while any material
has no project, without deleting records or inventing an assignment.
The batch records actor, request key/hash, source digest, preview hash, reason and
explicit mappings/reference context. Row snapshots identify each new material and
its physical source row. Unused cell values and original source files are not stored.
ORM listeners and PostgreSQL statement triggers reject UPDATE, DELETE and TRUNCATE.

Existing migrations through 0011 are unchanged. Fresh upgrade, upgrade from 0011,
Alembic current/heads/check and empty downgrade/re-upgrade are tested. Downgrade
refuses once import provenance exists; preserve that schema and use a forward
migration. Reverting application code can leave these additive tables in place.
Do not remove imported records, reset counters or erase number reservations as a
rollback shortcut. Production migrations remain outside this task's authorization.

## Verification

Full isolated Docker project `reawote-test-86961169dfcc424ebcdbca5e3e34bb2a` passed:
818 backend, 127 actual PostgreSQL (auth gate 27/27), 330 Linux worker and 290
existing frontend tests, plus lint and build; no skipped tests. The new import UI
was developed after that image was built. Its full local frontend run passed 323
tests; after adding bounded actionable error codes/coordinates, the focused
client/UI suite passed 53 tests and lint/build passed again.
PostgreSQL tests cover duplicate retries, competing actors/keys, ordinary creation
races, actual concurrent account revocation, consistent reference snapshots until
commit, direct SQL audit mutation rejection and refusal of destructive downgrade.
Unit/API cases also inject a final audit insertion failure and verify complete
rollback of materials, metadata, reservations, batch and brand counter.

Final E2E run `d4fcb3b3-cb1a-4f82-8372-9bba5b72e447` passed all 14 scenarios on
fresh data and all 14 after restart with retained data. It imports an actual BOM
semicolon CSV and an independently generated XLSX workbook, verifies exact
identities, default states, counters and two immutable batches. Desktop and
390px mobile preview/history screenshots were visually inspected. Table overflow
is contained within its scroll region. Console, network, response-body and
protected-resource gates remain enabled. No skipped E2E scenarios.

Original main, protected databases/volumes, backup/restore resources, production
NAS and live GCS/Notion were not used as test targets. Run `scripts/test.ps1` from
the owned worktree with the configured local Docker CLI to repeat these checks.
