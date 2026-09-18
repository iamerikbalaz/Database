# Company change history

Company creation and PATCH updates now save an immutable audit event in the same
transaction as the company write. Each event contains the company and actor IDs,
an ordered per-company change number, action, timestamp, exact descriptive values
before/after, and their SHA-256 digests. Unchanged PATCH requests create no event.
Failed validation, uniqueness conflicts or failed audit writes leave no company
change or success event. Company updates lock the row before reading its previous
values, so concurrent edits preserve a meaningful ordered history.

## Reading history

ADMIN can open **Company change history** in a company detail. It reads only when
opened and shows at most 20 events. Expand a change to inspect changed fields,
the responsible actor ID and values before/after it. **Older changes** requests
another page explicitly; **Latest changes** restarts from the newest records.
Switching companies or actors discards stale responses. Property values are text,
including URLs. Historical values do not claim to be the current company profile.

The corresponding authenticated route is
`GET /api/companies/{company_id}/history?after={event_uuid}`. The cursor is optional,
belongs to that exact company and is returned as `next_cursor` only when another
page exists. Records are ordered by descending version; future appends do not
change older pages. Responses are not cacheable. Non-administrators cannot read
this route, including production leads who retain their existing local edit role.

## Boundaries

- History covers company create/edit changes made through the application after
  this feature is installed. Existing records receive no invented past events.
- This is not privileged direct-SQL auditing, a complete system audit log, record
  deletion, a restore command or automatic Notion synchronization.
- Snapshots include only company descriptive fields, saved Notion page ID and the
  existing active flag. They contain no session, credential or raw external body.
- The schema reserves `NOTION_ADOPTED` for the separate reviewed-adoption command.
  Its evidence must bind the company's explicit page and only selected changed
  descriptive fields. That command remains pending; the current comparison only
  reads and never applies values.
- Existing company CRUD request bodies/response shapes remain compatible. Audit
  history does not add replay protection to legacy create requests; idempotent
  legacy CRUD remains a separate backlog item.

## Migration and rollback

Additive migration `20260918_0020` follows 0019. It creates `company_change_events`
and guards ordered inserts, exact after snapshots, source shape/selection and
append-only history. Foreign keys preserve company/actor references. ORM update
and delete are rejected, and PostgreSQL rejects UPDATE, DELETE and TRUNCATE of
history. A failure to write an audit event rolls back its domain transaction.

An empty history table can be downgraded to 0019 without changing existing
companies. Once history exists, downgrade deliberately refuses to remove it; use
a forward migration. Reverting only the application code would stop recording new
events while keeping existing evidence, creating a documented coverage gap. No
production migration was run. Test projects and previous-head upgrade checks are
isolated and use synthetic records only.

See `autonomous-pbr-progress.md` for exact test runs and outstanding checks.
