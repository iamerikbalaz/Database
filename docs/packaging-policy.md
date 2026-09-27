# Automatic global ZIP policy

Settings owns the shared date rule. The original archived `readme.txt` and both
`scripts/texture-zip.zip` scripts establish **4 March 2026**, with midnight in
**Europe/Prague** as the default boundary. Strictly before it selects method A;
the boundary itself and later selects method B.

- **A** uses the original legacy conversion method and normalizes archive dates
  to 1 January 2026. **B** uses the current method and retains packaging dates.
- The Settings page exposes the cutoff and IANA timezone to administrators. All
  authenticated roles can read the rule. Methods and their timestamp behavior
  are fixed compatibility parameters, displayed without unsupported controls.
- The source date is the first verified original master-folder modification time,
  recorded before staging. Later source scans/copies cannot silently replace it.
  A material without that observation uses its current validated original source
  inventory when its first batch is saved.
- Saving a CSV batch automatically selects its policy. No per-material selection
  or override step is required. Changes to Settings apply to future batches for
  both existing and new materials.

## Versioning and concurrency

Migration 0031 adds append-only `packaging_settings_revisions`. Every administrator
save checks the expected version and stores the exact actor-scoped idempotency
request and response. Retry returns the original response even after later edits;
reusing a key with a changed payload fails. PostgreSQL serializes writers with a
transaction advisory lock and enforces contiguous versions and immutable rows.
Auth, active-session checks, CSRF and current administrator authorization apply to
both initial writes and replay. The UI retains uncertain requests and blocks
navigation until the same request is recovered.

New schema-2 batch snapshots freeze the full global settings. A shared settings
lock precedes material locks, so an edit cannot change the rule during batch
creation. A changed settings revision appends an automatic material policy
successor; it can retain the same method or change timezone without rewriting any
previous decision. Existing administrator-override history is retained and future
batches use the global rule. The original per-material API remains compatible for
historical clients, but the ordinary UI no longer exposes manual ZIP policy work.

Existing batches, jobs, accepted output and downloads are immutable. New jobs for
a schema-2 batch bind that batch's recorded settings and policy, even after the
global rule changes. Already reserved jobs carry their original policy ID. Old
schema-1 jobs retain their previous current-policy checks. Source, ownership,
lease, account and archive checks remain in force in either version.

## Phase-one publication

Materials publication preparation includes **Check sources and review**. It runs
existing technical validation sequentially over the frozen selection and then
shows the CSV preview. A lost validation response retains its exact request key;
no later material starts until recovery. Leadership can run this automatic check.
It does not create a human approval or change the manual Checked property.

Publication batches no longer require technical, publication or content human
approval rows. Those optional IDs and old histories remain available. Current
successful technical reports are validated against their report hash, full source
inventory, material identity and revision; metadata snapshots must match their
source bytes and export fields. Content still needs valid credits, category and
brand references, and export warnings still require acknowledgment. Saving freezes
the complete content snapshot. A reason is optional and an omitted reason records
`Publication preparation`, not an invented approval. Existing batch CSV bytes and
hashes are unchanged.

## API

- `GET /api/settings/packaging`: current global rule and version.
- `POST /api/settings/packaging`: administrator save with `idempotency_key`,
  `expected_version`, `cutoff_date` and `storage_timezone`.
- Existing publication preview/create and packaging reservation routes consume
  the rule automatically. Packaging reservation may omit `expected_policy_id`;
  the server selects the policy bound to the saved batch, never a client policy.

## Verification and rollback

Portable tests cover roles, CSRF, invalid parameters, stale versions, immutable
receipts, exact retry, automatic selection, global changes for an existing material,
old-batch packaging after settings changes, and CSV/package preparation with zero
human approval rows. Frontend tests cover exact recovery, current-state refresh,
optional reasons and sequential source validation. Isolated PostgreSQL cases cover
competing settings writers, settings/batch lock order, policy-chain guards and
reservation of a historical batch after a changed cutoff. The browser publication
scenario uses automatic source checking and exercises global-settings lost-response
recovery while retaining old artifacts.

Migration 0031 is additive and preserves previous records. It makes the two human
approval IDs nullable and removes the requirement that every content snapshot have
a human content-approval row. Existing report/material foreign keys and append-only
history triggers remain. Downgrade refuses recorded global settings, automatic
policy evidence or new publication snapshots; use a forward repair once populated.
No production source, worker filesystem, live GCS or Notion operation is part of
this settings implementation.
