# Existing material and catalog history: cursor windows

The existing read routes accept optional `after=<record UUID>` and `limit=1..100`
(default 100), preserving their original JSON response shapes:

- `GET /api/materials/{id}/audit`
- `GET /api/materials/{id}/identity-operations`
- `GET /api/materials/{id}/identity-history`
- `GET /api/materials/{id}/content-history`
- `GET /api/materials/{id}/content-approvals`
- `GET /api/catalog-audit`

Use the last returned record's ID as `after`. Rows are ordered newest first by
`created_at, id`, both descending; content revisions use `revision, id` instead.
The UUID tie-breaker prevents timestamp ties from losing or repeating existing
rows. A newly inserted newer record does not shift the older cursor window.
These are live reads, not a transaction snapshot or total count. Concurrent inserts
that sort behind a cursor may appear on later pages. Reload latest to see newer
records or updated identity-operation status.

Fewer than the requested limit means the end of the currently available history.
A full final page requires one additional, possibly empty read. An unknown cursor,
or one belonging to another material, returns the same fixed
`409 HISTORY_CURSOR_INVALID`. Malformed UUIDs and out-of-range limits return 422.
Every request checks current authentication, role and material assignment before
looking up the cursor. Archived materials remain unavailable on these ordinary
material routes. Catalog audit remains limited to ADMIN/PRODUCTION_LEAD. Cursors
are positions, not capabilities or authorization grants.

The source-review, identity-operation, content and approval panels expose explicit
Older/Latest controls. Each page is bounded to 100 records. These reads never
replace the parent's current review, editable draft, approval or active-operation
controls. A reload of history alone does not revalidate those current controls;
use the separate current-status reload action. Catalog audit and identity-change
history have paged APIs; no new catalog or identity-history screen is introduced.

Client parsers reject oversized lists, duplicate IDs, cursor repetition and
cross-material content/identity snapshots. Actor/target changes retire pending
history reads; source/current initial reads are also scoped to the actor. A
transient read failure keeps the current page, while an authorization denial
hides it. No automatic writes, persistence or mutation replay is added.

No schema migration or data rewrite is required. Rolling back this slice restores
the old first-100 behavior; it does not remove history. Tests cover >100 records,
timestamp ties, subsequent insertion, foreign/invalid cursors, bounds, changed
roles/assignment, archived visibility and actual PostgreSQL UUID ordering. Browser
coverage creates 103 synthetic content revisions through the real API and verifies
Older/Latest navigation on fresh and retained data while preserving draft 103.
