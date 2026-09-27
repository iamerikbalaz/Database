# Editable source metadata

Material detail edits the existing metadata properties: Color HEX, sample width
in centimeters, and sample height in centimeters. Saving updates or creates only
the linked material folder's root `metadata.txt`. Identity, master resolution and
other source keys cannot be edited through this endpoint. Unknown JSON keys and
exact JSON numbers are preserved. A supported legacy dimensions-only text file
is converted to the supported JSON structure; its entire original text is retained
under `LEGACY_SOURCE_TEXT`. Malformed JSON, manifests and incompatible container
types are rejected without changing the source. Blank fields explicitly remove
that supported property. Dimensions are positive and fit Numeric(12,4), without
rounding; HEX is normalized to `#RRGGBB`.

## Authorization and proof

`GET /api/materials/{id}/source-metadata` inspects the live source and reports
availability, editability and the explicit source-write setting separately.
Unavailable sources display their last database values with a clear notice.
`POST` accepts all three values, `expected_sha256` (null means file absent), the
material's `expected_updated_at`, and an exact-request `idempotency_key`. An
active editor role and current material assignment are checked on the server.
Source writes require `SOURCE_MUTATIONS_ENABLED=true` in backend and worker,
the private worker mutation credential and a separate private journal root.
The backend checks live availability, file hash and the worker write gate before
reserving an operation, then rechecks authorization and the material revision.
Known unavailable or read-only sources therefore leave no pending operation.
The Windows inspection bridge does not grant write capability; the writer
requires Linux descriptor-relative no-follow operations and fails closed on
unsupported platforms.

Before worker dispatch the backend commits a durable operation, holds ownership
of the material and source tree, invalidates old technical approvals, resets
Checked, and appends an audit event. Operations block folder/identity changes,
staging and packaging until a verified terminal outcome. Ambiguous responses
remain RUNNING and retain their original UUID and request; the explicit resume
endpoint reconciles that same journal. Reloading the page discovers the active
operation. A known completed request returns its receipt without writing again.

The worker bounds source files and output to 4 MiB, refuses symlinks, hardlinks,
special files and traversal, checks file identity and expected bytes, and uses an
exclusive same-directory temporary file plus fsync and atomic replacement. A
missing destination uses an atomic no-replace rename. Folder and temporary-file
identities are rechecked before replacing; output bytes are verified afterward.
A filesystem must support these Linux atomic operations. In particular, a Docker
Desktop Windows bind can reject no-replace rename even when reads and ordinary
replacement work. Use a compatible source mount; an uncertain operation remains
owned and must not be cleared by editing database state.
A durable PREPARED checkpoint allows recovery after replacement but before a
response. Private journal storage never overlaps the source. File permissions
are preserved on replacement. Newly created metadata uses mode 0644 so a separate
packaging service account can read it. These safeguards assume an administratively
controlled source filesystem; arbitrary concurrent external edits are rejected
when observed and are never silently merged.

On confirmed success only, the backend stores a new immutable internal metadata
snapshot and advances current values. The operation authorization and terminal
receipt are immutable; audit events are append-only. Neither metadata snapshot
history nor private source bytes are exposed in this editor. Publication content
remains a separate database workflow.

## Database-only historical import

`persist_metadata_snapshot` accepts validated values and explicit provenance.
Callers own authorization, the material row lock, audit and transaction. Workbook
imports leave source filename, hash and content null; a WARNING explains the
historical evidence. The helper never writes or asserts inspection of metadata.txt.

## Verification scope

Automated tests use generated files in owned container temporary directories,
isolated SQLite/PostgreSQL fixtures and mocked HTTP boundaries. The local
acceptance exercise additionally creates and edits a synthetic material on an
owned Linux volume and verifies exact replay, unknown-key preservation, file
permissions and unchanged texture/preview hashes. No original NAS source writes
or external integrations are performed by this verification.
