# Source inventory contract

The worker's read-only `POST /internal/material-inventory` accepts the same
relative `folder_path` as preflight. It returns version 1, the folder basename,
master resolution/time, existing ZIP policy, ordered entries, total file bytes
and a SHA-256 source revision. Entries contain relative path, file/directory kind,
size and file SHA-256. File contents and host paths never leave this endpoint.

The revision is SHA-256 of UTF-8 JSON containing exactly `schema_version`,
`folder_name`, `master_resolution`, `policy` and `entries`, sorted object keys,
ASCII escapes and separators `,` and `:` without spaces. Entries sort by path.
Empty directories are included. File timestamps do not affect the revision;
the master directory timestamp affects it only through the existing ZIP policy.
All source content is included conservatively, including metadata.txt, all map
resolutions, SOURCE and PREVIEW. Adding, removing or renaming files invalidates
the revision. No generated output may be written into this source tree.

Linux descriptor-relative no-follow access is mandatory. Links, hard-linked
files, device files/FIFOs, cross-device entries and unsafe names are rejected.
Each descriptor and directory entry is checked before/after reading; a second
stat walk and re-open of the requested material detect replacement or changes
during the scan. All failures return a safe code and no partial inventory/hash.
The preflight's existing nonblocking treatment of metadata remains unchanged.

Limits: 20,000 entries, depth 16, 64 GiB per file, 256 GiB total and a 120-second
cooperative deadline checked between filesystem operations. This deadline does
not interrupt a blocked OS/NAS read. Files stream in 1 MiB chunks.

This is an observed revision, **not an atomic filesystem snapshot or lease**.
External edits can occur after any scan. Approval must rescan and match the
reviewed revision; publication must copy from validated descriptors into its own
immutable staging area and verify every copied hash before using approvals.
An inaccessible or changing source must never be treated as approved/current.

The endpoint is internal; normal clients use the authenticated backend. Worker
ports must not be exposed beyond the local/internal application network.
Technical image checks and approvals are described in `technical-validation.md`
and `material-approvals.md`. Publication consumption is subsequent work.
Inventory alone does not assert that image files are valid.

## Authenticated application workflow

Migration `20260915_0007` adds `material_inventories`, `material_review_states`
and `material_audit_events`. Existing materials start without an observed
inventory; no production files or previous metadata snapshots are changed.
PostgreSQL triggers reject UPDATE, DELETE and TRUNCATE of inventories/events.

The backend validates the worker's canonical hash, ordered unique safe paths,
parent directories, sizes, highest master resolution and response size (64 MiB).
It combines that file hash with the material UUID, technical identity, relative
folder, name, category, project, brand and assignment into the application
`revision_hash`. A monotonic generation additionally prevents a reopened asset
from inheriting an earlier approval when its file bytes remain identical.

| Endpoint below `/api/materials/{id}` | Meaning |
| --- | --- |
| `GET /review` | Last observed revision, generation, timestamp and failure code |
| `GET /inventory` | Review plus current file inventory, or null after invalidation |
| `POST /inventory/scan` | Fresh worker scan, immutable snapshot and audit event |
| `POST /reopen` | DONE → IN_PROGRESS with a required reason |
| `GET /audit` | Most recent 100 review events, actors and safe details |

Both POST bodies require UUID `idempotency_key` and integer
`expected_generation` from the last read. Reopen also requires `reason`
(1–2000 characters). Repeating the same actor/key/body returns the recorded
result without repeating work. Reusing a key for different input returns 409.
An outdated generation or concurrent material edit also returns 409. Authorization
and assignment are checked again after a slow worker scan, under the final row
lock. No worker access occurs before the initial authorization check.

Processors may scan assigned materials; leads and administrators may scan all.
Only leads/administrators reopen. All four roles may read within their existing
material visibility. CSRF and trusted origin remain required for every mutation.

An unchanged scan retains the generation. Changed contents, managed material
fields, folder relinking, Done, reopen or a failed scan invalidate an existing
review. The online existence flag remains true; its status changes to
PUBLISHED_UPDATE_REQUIRED when appropriate. External edits are detected at the
next scan, so the UI calls the result observed rather than continuously verified.

Reopen clears current metadata to NOT_SCANNED but retains the linked path and
all immutable metadata snapshots. A later Done adds the next snapshot. The
frontend requires a reason, reloads state and retains request keys across unknown
network outcomes. It displays inventory entries in batches of 100.

This audit covers review operations and changes that invalidate an existing
review. General account/catalog/create audit and full historical browsing remain
separate backlog items. Technical and publication decisions have their own
revision-bound history under migration 0008; see `material-approvals.md`.

## Upgrade and rollback

For an explicitly selected local/test database, normal backend startup runs
`alembic upgrade head`. Tests cover fresh upgrade, upgrade from auth head 0006,
current/heads/check, and downgrade/re-upgrade in an isolated database.
Use an application/database backup before any later authorized deployment.
To roll code back, stop the new application and select the earlier commit.
Leaving the additional tables is harmless to the earlier code. An explicit
`alembic downgrade 20260914_0006` removes new review/inventory/audit history;
it preserves material records, auth data, metadata snapshots and source files.
Do not downgrade a live database without a separately authorized maintenance plan.
