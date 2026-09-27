# Publication candidate preflight

`POST /api/publication-batches/preview` accepts `material_ids`, an explicit unique
selection of 1–100 UUIDs. It requires an active administrator or leadership session,
trusted Origin and CSRF. It reads only database records and creates no batch,
artifact, audit entry, published state or filesystem/network operation.

Selection is sorted by UUID before taking material locks under the shared account,
global packaging settings and catalog gates. The response contains a deterministic preview hash and each
material's export row, snapshot hash, current source/content hashes, errors and
warnings. It excludes source paths/raw metadata and private snapshot evidence.
Access/session expiry is rechecked after domain work. Invalid request responses
use the existing static-schema sanitization and never reflect arbitrary values.

A preparable candidate requires all of the following:

- Production Done and no active/recovery-required identity operation.
- Current successful technical check and inventory for the same generation and
  source revision, with matching check/inventory references and no failed scan.
- A valid complete technical report whose saved hash, image facts, inventory and
  material context match the current revision. Phase one requires no human
  technical, publication or content approval rows.
- Current content, active brand/vocabulary, categories and integer credits.
  Empty description/tags remain visible warnings.
- An immutable metadata snapshot matching current metadata, including master
  resolution. Its `metadata.txt` SHA-256 must equal the file recorded in the
  validated source inventory. Older normalized dimensions/color cannot silently
  accompany a newer source revision. Missing color/dimensions block export even
  though missing metadata remains nonblocking for production Done.
- Valid values for the exact CSV serializer and no case-insensitive duplicate
  technical identities within the selection. Existing technical identities retain
  the database's 512-character bound; display names/brand identifiers remain 255.

The internal snapshot binds material context, content and optional historical
decision IDs, source/check/report references, metadata snapshot/hash, the canonical
CSV row and the global packaging settings revision.
It omits raw metadata. Future batch creation must recompute this snapshot under
the same locks and compare the displayed preview hash before saving anything.

This is a database-state preflight. It does not freeze NAS files or promise that
those files are still current on disk. Packaging must stage and verify files
against the captured source inventory before using them. See `publication-plan.md`
for immutable database batches, job execution and later integration work.

Materials offers **Check sources and review** to run the existing read-only worker
validation for each selected material before loading this database preview. It
records technical facts and never changes the manual Checked property. See
[the automatic policy contract](packaging-policy.md) for phase-one publication,
idempotency, global settings and retained history. Verification is tracked in
`autonomous-pbr-progress.md`.
