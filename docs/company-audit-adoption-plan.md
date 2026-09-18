# Company history and reviewed Notion adoption

## Scope and product assumption

The configured company projection has explicit field IDs, but no automatic source
ownership policy. Keep all synchronization manual: ADMIN reviews a comparison and
may adopt selected descriptive fields into the local company. No remote writes,
new records inferred from names, project relations, active flags, role changes or
Notion link changes belong in adoption. Existing catalog managers keep their current
local edit permissions; only ADMIN can initiate an external read/adoption.

## Small implementation sequence

1. Add migration 0020 and a `CompanyChangeEvent` model for append-only company
   history: company/actor foreign keys, ordered per-company version, action,
   explicit before/after snapshots and hashes, optional request binding and minimal
   verified Notion provenance. Preserve existing records without inventing past
   events. Block deletion/truncation/mutation of history and destructive downgrade
   while evidence exists. Existing migrations through 0019 remain immutable.
2. Record company create/edit changes atomically with their domain write. Acquire
   the company row lock before a change and its history version allocation. Add a
   bounded, explicitly paged ADMIN history route. Whitelist descriptive snapshot
   fields; never store credentials, raw Notion responses or unrelated properties.
   This is application/API history, not a claim to capture privileged direct SQL.
3. Add an idempotent ADMIN adoption command containing an exact page/local/remote
   comparison binding, selected mapped fields and reason. Replay an already committed
   actor/key/body before any remote request, including when the integration is later
   disabled. Reject changed bodies or unrelated companies. Re-read Notion with no
   database transaction held, revalidate the reviewed observation hash, then lock
   the current company, reauthorize and verify the exact local snapshot. Commit only
   selected changed fields and the immutable event in one transaction. Failed reads,
   stale comparisons or revoked accounts produce no company mutation or false
   success event. A later remote edit cannot be prevented by this read-only adapter;
   the event records the precise observation actually adopted.
4. Add explicit per-field review and confirmation to the comparison panel, durable
   result/history display and exact request retry after unknown outcomes. Refresh
   the company after success; historical event success never implies its values are
   still current. Keep the existing comparison-only behavior available.

## Verification and rollback

Use synthetic HTTP only. Cover roles/CSRF, selected nulls, unrelated/replayed keys,
local/link/schema/remote changes, disabled integration and interrupted reads. Actual
PostgreSQL tests must race duplicate adoption requests with local edits and account
revocation, verify the append-only history constraints, and check fresh/previous-head
upgrade, Alembic head/current/check and rollback guards. Run affected API, frontend,
full regression and isolated browser tests on fresh and retained data. Browser
intercepts, if used for remote values, must remain explicitly distinguished from
actual enabled-deployment integration tests.

Rollback disables adoption and preserves company/history rows. After real use,
prefer a forward fix; do not erase audit evidence. Live mapping verification,
additional resource audit coverage, legacy CRUD idempotency, soft-delete/restore and
temporary-derived-file lifecycle remain separate backlog items.
