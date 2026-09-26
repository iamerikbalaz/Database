# Catalog database

The catalog has fixed **Online categories** and **Brand collections** tabs. Search matches names, persisted abbreviations and collection brands. Active state, creation date range and collection brand can be filtered; name, abbreviation and creation date can be sorted. All matching records are loaded, so bulk actions target the entire filtered set.

Columns are Name, Abbreviation, Type, Created and Active, plus the owning Brand for collections. Visibility preferences are saved per tab. Administrators and production leads can edit Abbreviation and Active in the table or apply a reviewed change to selected / all filtered records. Other roles can browse and filter.

## Stable vocabulary identities

Canonical names, collection ownership, type and creation date remain read-only. These values identify immutable publication drafts, approvals and exports. **Create replacement** pre-fills a new entry of the same type and brand; the operator enters the replacement name. Creating the replacement does not change existing material assignments or deactivate the original. Retiring the old value is an explicit Active change.

Abbreviations accept uppercase A–Z, digits, underscore and hyphen, beginning with a letter or digit, at most 32 characters. Empty means no abbreviation. Category codes are globally unique; collection abbreviations are unique within one brand. This means applying the same nonempty abbreviation to several categories produces a per-row conflict; clearing codes or changing Active can apply to the entire selection.

Every edit has a reason, expected record version and idempotency key. The generic table fixes IDs, versions, values and the save callback before a bulk action. It reports each result separately, stops continuation after session changes, and pauses an unknown write for same-key retry. Replayed writes do not double-apply; successful writes are not retried. Refresh reloads the records and reapplies filters. Current pending requests are held in memory; do not close the page during an unresolved write.

Changes to Active or Abbreviation invalidate checks and approvals on linked materials. Existing assignments and immutable history remain intact. A material with an active source operation must be reconciled before its catalog value changes.

## Migration 20260926_0029

Depends on project-folder migration **20260926_0028**. Adds nullable abbreviation columns to both tables and seeds all matching values from the 2026 workbook mapping. It preserves existing names, IDs, activity, versions, timestamps and assignments. Legacy/custom values remain without a code until edited.

Code and creation time appear only in catalog administrative responses. They are deliberately excluded from publication content snapshots, so applying this migration alone does not alter approval hashes. Old create-request hashes remain compatible when the new optional abbreviation is omitted.

Database guards still reject name/owner/date changes and deletion. Active or abbreviation changes require a version increment. Audit events remain append-only. Clean downgrade can remove reconstructible seeded codes; handwritten codes or table-edit receipts require a forward migration.
