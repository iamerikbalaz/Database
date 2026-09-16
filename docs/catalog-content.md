# Catalog vocabulary and publication drafts

Migration 0010 introduces online categories, brand collections, material links,
publication content and append-only revision/catalog audit history. Existing
materials start with an empty draft; no categories or company/project mappings
are inferred. The main storage category remains separate from online categories.

## Product rules

- Categories can be shared by brands. A collection belongs to one brand; a
  material may use multiple categories and multiple collections of its own brand.
- Individual values use Unicode NFC, collapsed ordinary whitespace and a
  case-insensitive uniqueness key. Colons and Unicode control/invisible characters
  are rejected. Categories/collections are limited to 255 characters; individual
  tags to 100, with at most 100 values of each kind per material. Tags are stored
  individually, deduplicated case-insensitively, with the first spelling retained.
- Catalog names and collection brand ownership are immutable. Deactivate a value
  and create a replacement to correct it; old assignments/history remain intact.
  New saves reject inactive values. Users can explicitly remove retired values.
- Credits are optional until publication validation, then a nonnegative integer
  (maximum 2147483647). Plain-text descriptions are optional and limited to 10000
  characters. Manual drafts can receive an explicit decision under migration
  0011; see `content-approvals.md`. AI draft provenance remains separate work.
  Content approval alone does not make a material ready for publication.
- Content changes invalidate source checks and material approvals, including the
  published-update-required state. Identical saves are no-ops for revision and
  generation. Catalog activation/deactivation invalidates affected materials in
  the same transaction. This does not delete snapshots or membership.
- Rebranding with assigned collections is blocked before worker IO. Remove the
  old brand's collection assignments explicitly, then review a new identity plan.
  Active identity operations block content writes and affected catalog changes.

## Authorization and concurrency

All routes require real sessions, completed password changes, CSRF and trusted
Origin for writes. Production leads/admins manage vocabulary; assigned processors,
leads/admins edit material drafts. Leadership reads drafts/history and can approve
the saved content together with admins. Audit viewing
for global catalog changes is restricted to leads/admins.

Vocabulary writes use the existing exclusive account/domain advisory gate so
retirement cannot race a new material assignment. Normal content writes use the
shared gate and lock the material. Explicit expected versions/revisions prevent
lost updates. Durable actor-scoped idempotency keys return the original outcome;
reusing a key for different inputs fails. PostgreSQL protects catalog identity
and rejects audit/revision updates, deletions and truncation. No hard-delete API
exists for catalog values.

## API and UI

- `GET/POST /api/online-categories`, `GET/POST /api/collections` (optional read
  `brand_id` filter); `PATCH /api/{online-categories|collections}/{id}` changes
  availability with expected version, idempotency key and reason.
- `GET /api/catalog-audit` returns the latest 100 events.
- `GET/POST /api/materials/{id}/content` reads/saves the complete draft; saves
  require expected revision, idempotency key and reason.
- `GET /api/materials/{id}/content-history` returns the latest 100 immutable
  snapshots, including exact category/collection values at the time of save.
- Catalog navigation manages vocabulary. Material detail edits content, shows
  retired assignments and loads historical versions. Unknown network outcomes
  freeze inputs and retry the same request/key. Reload explicitly discards edits.

Pagination beyond the latest 100 history records and beyond 10000 vocabulary
values in the client remains backlog. Invalid request responses do not echo
submitted descriptions, arbitrary field names or values.

## Migration and rollback

Only migration 0010 is new; 0001–0009 remain unchanged. Fresh and prior-0009
upgrades must retain material UUIDs, identities and the number ledger. Downgrade
to 0009 removes vocabulary, memberships, drafts and their history; export/retain
them before a deliberate rollback. It does not alter files, original metadata,
identity journals or existing number reservations. Run migrations only on the
intended owned test database until a separately authorized deployment.
