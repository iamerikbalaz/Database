# Customers, Orders, and outbound synchronization

The application owns customer/order properties. The one-time bootstrap is a
separate, reviewed import; the live integration never copies Notion values back.
Notion page bodies, client comments, templates, covers and shared content are not
modified by property synchronization.

## Configuration

Both external capabilities are disabled by default. The ordinary Customers and
Orders APIs continue to work locally and save pending Notion work in the same
database transaction as each record change.

- `NOTION_OUTBOUND_ENABLED=true` enables dispatch to the official Notion API.
- `NOTION_ACCESS_TOKEN` must contain a separately supplied integration credential
  with read, insert and update content access. Keep it in the private runtime
  environment; do not commit it, put it in a browser, or copy a connector session.
- Share Customers, Orders and the related People database with that integration.
- `NOTION_CUSTOMERS_DATA_SOURCE_ID` defaults to
  `276a8a19-ae7b-8050-9bc1-000b280fc7ca`.
- `NOTION_ORDERS_DATA_SOURCE_ID` defaults to
  `dcfda230-c4f7-4207-817a-07643eaf4dbd`.
- `ORDER_FOLDERS_ENABLED=true` and an explicit absolute `ORDER_FOLDERS_ROOT`
  enable folder creation. The approved desktop root is `R:\0. PROJECTS`.
  The runtime account must have access to that mapped drive. Tests use private
  temporary directories only.

The older `NOTION_ENABLED` reader flag does not enable outbound synchronization.
No credentials are available merely because Codex can read Notion through its
connector. Disabled synchronization is explicitly shown in API/UI state.

## Field ownership

Customer writes include Name, Status, Website, Address, Shipping address, Legal
name, VAT ID, Company describtion, Notes, and Brand Identifier. Contact fields,
Product type and RWT Categories are excluded. The nullable customer identifier is
used; internal legacy brand placeholders are never exported.

Order writes include Number, Customer, Project type, Starting date, Due date,
Note, Responsible, Status and Priority. Generated Name and Cutomer rollup remain
Notion-calculated fields. The app's folder name uses the same uppercase join:
`Number_Customer Name_Project type_MMYYYY`. Spaces are preserved. Invalid Windows
filename characters are rejected, not silently changed to a different name.

Existing Responsible relation lists are preserved, including multiple People
pages. An explicit processor change replaces the list with that processor's
mapped People page; clearing it sends an empty relation. A changed processor
without a mapping leaves synchronization in an actionable error state. An
unrelated edit does not clear an unmapped Responsible property.

## Durable work and recovery

`notion_sync_states` coalesces changes by entity. The worker acknowledges a
specific revision; an edit made while it is sending remains pending. Orders wait
until their customer has a Notion page. Existing pages are checked for the correct
data source before property updates.

Creation first checks Brand Identifier (or Name when the identifier is empty), or
order Number. A matching unlinked record is not overwritten or duplicated: it
requires explicit linking during reconciliation. HTTP rate limiting and safe
update failures can retry. A timeout/server failure after a create, or a crash
before its acknowledgement, becomes `RECONCILE`; neither retry nor a later edit
may blindly create a second page. An operator must inspect the Notion result and
resolve the link before resuming. No partial Notion response or credential is
included in an error message.

The dispatcher holds a PostgreSQL advisory lock, so a second application process
cannot dispatch the same queue. Pending work retains its author, and dispatch
checks that the author is still active and allowed to manage the directory.

Folder work uses `order_folder_operations`. Only a new app order calls its create
hook; the bootstrap does not. Renaming is a separate confirmed, revision-checked,
idempotent command. The service rejects paths outside the configured root,
symlinks/junctions, existing destinations and colliding four-digit numbers. It
does not recursively move/delete anything. A crash leaves the operation for
reconciliation instead of adopting an arbitrary existing folder. Successful
creation/rename updates the order path and appends its author to order history.

## API surface

- `GET /api/notion-sync/{CUSTOMER|ORDER}/{id}`: state and enabled flag.
- `POST /api/notion-sync/{CUSTOMER|ORDER}/{id}/retry`: managers only; refuses
  ambiguous creates and running operations.
- `GET /api/orders/{id}/folder`: folder operation state and enabled flag.
- `POST /api/orders/{id}/folder`: managers only, CSRF protected, with
  `Idempotency-Key` and body `{action, expected_updated_at, confirmed: true}`.
  `action` is `CREATE` or `RENAME`; the destination name is always calculated by
  the application, never accepted as a free filesystem path.

Tests cover fake Notion requests, preserving client content and multi-person
relations, disabled dispatch, ambiguous create recovery, concurrent edits,
authentication/CSRF, folder collisions, stale plans, identity checks and exact
request replay. They perform no live Notion writes or production NAS mutations.
