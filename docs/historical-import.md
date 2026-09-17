# Historical PBR import

The administrator-only `/imports` page and API provide inspection, preview,
atomic confirmation and immutable batch history. Synthetic CSV/XLSX sources are
tested, including actual browser uploads and a restart with retained data. This
is not evidence that any real historical workbook or production NAS has migrated.

## Browser workflow

Choose a file and its explicit CSV delimiter or XLSX worksheet, select the five
source columns, and map each literal label to an existing record. Review the
preview, provide a reason and acknowledge that the imported records still need
normal source checks and approval. Any source or mapping change invalidates the
preview. Tables show project company and brand company separately and paginate
large batches. Batch history is also paginated and links to current materials.

An unknown confirmation outcome freezes the exact request and its original
idempotency key for retry. The UI validates the returned batch against that
preview before reporting success. A definite rejected request requires another
preview. Source bytes remain in page memory; leaving or reloading the page loses
that local retry state. Check saved batch history before starting a replacement
import after such a navigation.

## Source and mapping

See [source limits and unsupported workbook structures](historical-import-plan.md).
Choose identity, name, project, brand and processor columns explicitly; five
distinct headers are required. Map each literal project/brand/processor label to
an existing UUID. No resources, companies, folders or online assets are inferred.
Project company and brand company may differ and are shown separately in preview.
Both associated companies, the brand and the assigned processor must be active;
the selected processor must have the PROCESSOR role. Historical project status
is not imported and does not itself prevent import into an existing project.

The exact identity must have its selected brand's prefix, a four-digit number
0001–9999 and the canonical uppercase category suffix. Prefixes may contain
underscores but cannot contain path separators, colons or control characters.
The number must be absent from existing materials and the permanent reservation
ledger. Duplicate brand numbers inside a file and active brand identity operations
block the whole batch. Unused columns are identified but their values are ignored.

Imported materials receive new UUIDs, preserve their historical identities and
start IN_PROGRESS / NOT_CHECKED / NOT_PUBLISHED, with no linked folder and empty
NOT_SCANNED metadata. Normal folder linking, inventory and approval remain required.
Numbers below a brand's counter may be imported only if never reserved or used.
Each counter advances to max(current, highest imported number + 1); 9999 exhausts
ordinary allocation at 10000. Counters never move backwards.

## API

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

Confirmation takes the exclusive application access gate and locks affected brands
in UUID order. Materials, metadata, reservations, counters and audit are committed
together. No filesystem or external service is involved. Retrying the identical
actor/key/payload returns the original result, including after later material edits.
Changing the payload for a previously used key returns a conflict. A lost response
must be retried using the original input and key, not a new key or a partial batch.

## Audit and rollback

Migration `20260917_0012` adds `material_import_batches` and `material_import_rows`.
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
