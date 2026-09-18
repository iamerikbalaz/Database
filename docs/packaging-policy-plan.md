# Persisted ZIP policy decisions

The first packaging-policy decision belongs to a material UUID and survives later
inventory scans, source timestamps, reopen and identity changes. Existing workers
report an observed policy; their read-only observations do not replace a saved
decision. Preserve the current enum CURRENT_ON_OR_AFTER_2026_03_04.

Add migration 0016 with append-only versioned policy decisions, same-material
predecessor/inventory references and database mutation guards. Initial selection
requires a current successful technical report for a DONE material and the exact
expected inventory/generation/revision. Persist the source observation, its digest,
configured storage timezone, boundary and chosen policy. Backend and observed worker
classification must agree. No migration backfills a decision from old timestamps.

Only publication approvers may initialize. A later initialization request reuses
the stored decision without classifying again. Administrators alone may override.
An override requires a server preview hash, expected current decision, explicit
different policy and a nonempty reason. Preview identifies the material/review and
published flag and explains that all current approvals will be invalidated; old
CSV batches/artifacts remain immutable. Confirm rechecks the exact state under the
material lock, appends a decision and audit, invalidates review and keeps any
published flag with update-required status. Exact requests replay their original
response after later changes; conflicting keys fail.

Reads follow ordinary role/assignment access and bounded cursor pagination.
Mutations require real session/CSRF, domain locks and no active identity operation.
A later execution coordinator must snapshot the decision ID/revision/policy/timezone
into each job and block override while it owns the material. This slice creates no
execution endpoint and changes no NAS timestamps.

Test first/repeated selection across changed observations, stale/corrupt inputs,
explicit boundary/timezone, admin preview/override/replay, permissions/CSRF,
invalidation and immutable history. Verify real PostgreSQL concurrent initial and
override decisions, immutable triggers/FKs, fresh/prior migration and populated
downgrade refusal. UI and retained-data browser verification follow the API.
