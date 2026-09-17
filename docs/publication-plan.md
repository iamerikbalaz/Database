# Publication: bounded implementation sequence

This implements sections 14/16 of `codex-handoff-prompt.md` against the current
application contracts. It does not authorize real publication, production NAS
changes or live GCS/Notion writes. None of those will be run in this task.

## 1. Exact deterministic CSV serializer

Accept immutable, validated row snapshots, never ORM objects or a live query.
Use the exact nine columns, semicolon delimiter, UTF-8 BOM, CRLF/RFC4180 quoting,
canonical decimal centimetres, uppercase color and integer credits. Deduplicate
individual categories/tags without permitting colon/control separators. Produce
the CSV hash and a hash of every complete row snapshot, retaining source and
approved-content hashes. Empty description/tags produce explicit warnings.
Reject duplicate materials/identities and overlarge batches. Internal UUIDs/hashes
belong in the manifest, never in the nine-column CSV. This alone grants no export
or publication permission and is not a complete publication feature.

## 2. Audited database batch creation

Leadership/administrators may prepare a batch, following current publication and
content approval rights. Production leads/processors get no new sensitive rights.
Use an explicit bounded material selection and expected review/content digests;
project selection must first resolve to a reviewable explicit list. Recheck real
account/session access, acquire material locks in stable UUID order, and freeze
the exact matching material, metadata, inventory, technical, publication and
content approval references in one transaction. Require production Done, current
technical/publication/content decisions, export fields and no active identity
operation. Missing/changed inputs reject the entire request. Catalog/brand writes
must remain serialized by the existing domain gate.

Use a new immutable batch/item history and actor-scoped idempotency, including
original CSV bytes/hash and full input/row hashes. Exact retry returns the original
batch after later material edits; a different body with the same key fails. Old
batches remain history and cannot silently become current publishable inputs.
Read/download requires current authenticated publication rights. Preparing or
downloading CSV does not set published state. New migration only, with populated
downgrade refusal; real PostgreSQL migration, concurrency and immutable-history tests.

## 3. Packaging and execution

A later worker step must independently stage verified immutable source files and
match them to the selected revision. A database lock does not freeze NAS contents.
Use the observed historical archive contract in `legacy-packaging-contract.md`,
isolated synthetic files, bounded conversions, manifest/ZIP validation and hashes.
Keep staging outputs away from sources. Record attempts separately from immutable
inputs, reject stale batches before new execution and serialize identity changes.
Do not present CSV preparation as proof that packaging or upload succeeded.

## 4. Integration and operator workflow

Only after verified packaging: configurable GCS adapter, isolated job-prefixed
objects, guarded completion and manual online-import confirmation. No production
credential or live upload is available/authorized here. Implement contract tests
and operational guidance; report live checks as unverified. Online importer
identity matching, real golden packaging assets and the exact deployed GCS layout
remain external verification gaps. Published identity changes stay blocked.

Add selection/preflight UI, immutable batch/history views and retained-data E2E
incrementally. Local creation/CSV tests do not close the full publication backlog.
