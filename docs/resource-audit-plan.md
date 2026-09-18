# Remaining resource history and material lifecycle

## Next small slice: ordinary resource changes

Company creation/edit/adoption now has ordered immutable evidence. The remaining
ordinary brand, project, internal-user and material create/PATCH routes still need
equivalent before/after history. Existing specialized material review, identity,
content, import, AI and publication histories remain authoritative for those
operations; do not relabel partial coverage as a complete system log.

Add a new migration after immutable 0020 for append-only resource change events.
Use explicit target foreign keys, an actor foreign key, a per-target version,
whitelisted before/after snapshots and their digests. Each event must reference
exactly one target of its declared kind. Preserve target/actor references and reject
history UPDATE/DELETE/TRUNCATE. Do not invent events for existing rows. Destructive
downgrade must refuse when evidence exists.

Integrate each ordinary write with its event in the same transaction, under the
existing access/domain lock order. Ignore genuine no-ops. Never snapshot password
hashes, session tokens, credentials, worker bodies or arbitrary relationship graphs.
Use explicit ADMIN-only paged reads and a reusable history UI. Add one resource at
a time with role, rollback, uniqueness and actual PostgreSQL concurrency coverage.
Credential provisioning/reset and account self-service actions need separate safe
event metadata; do not claim their coverage from an ordinary user PATCH event.

## Following slice: reversible material archive

The handoff names soft-delete/restore without defining cascade or ownership rules.
Choose the conservative first scope: explicit ADMIN-only archive and restore of
individual PBR database records. Preserve every row, technical identity, number,
source folder, immutable history, package and cloud reference. No filesystem or
external deletion is implied. Companies/brands/projects are separate later scopes;
their existing active flag is not a hidden cascade-delete command.

An archive command needs a current local/version binding, reason, acknowledgment
and an actor-scoped idempotency key. Reject published materials and active or
unresolved identity/packaging/staging operations. Archive and restore invalidate
current decisions; restoring does not reinstate old approvals or assert that NAS
files still exist. Require a new source/technical/content review before publication.

Archived records must be excluded from ordinary lists and rejected by new domain,
worker and service-credential operations. ADMIN gets explicit bounded archive
listing/history/restore. Historical publication/export and accepted-package evidence
must remain readable under their existing permissions. Audit all access paths,
including resumed jobs, batch validation, preview downloads, service credentials,
catalog invalidation and explicit replay, before enabling the feature.

Verification must race archive against edits, source IO, reservations and account
revocation on real PostgreSQL. Check fresh/prior migration and rollback guards,
identity/number preservation, immutable historical downloads and fresh/retained
browser behavior. Production migrations, source cleanup and cloud deletion remain
outside this development run's authorization.

### Source-level integration map (reviewed before implementation)

| Surface | Required archive behavior |
| --- | --- |
| `auth/access.py::require_material` and `api/material_review.py::_material` | Default denial for archived materials; explicit narrow exceptions for authorized historical reads. Do not make ADMIN a blanket bypass for new work. |
| `api/resources.py` | Exclude archives in lists; guard ordinary detail/metadata/PATCH; keep identity and number uniqueness across archived rows. |
| `api/material_operations.py` | Both pre-IO `_get_material_revision` and post-IO/final transaction must detect archive; no source findings returned from stale authorization. |
| `api/material_identity.py::_finish` | Existing durable ownership must block archive while active/recovery-required. Finish an already authorized filesystem transaction consistently even if its actor is revoked. Do not interrupt recovery halfway. |
| `material_identity.py::require_material_idle` | Reuse identity, packaging and staging ownership checks under the material row lock; do not equate a network timeout with a completed owner. |
| `packaging_jobs.py` and `api/packaging_jobs.py` | Reject new reservation/dispatch/current-input acceptance on archives; preserve factual late worker observations and exact recovery semantics. |
| `publication_preflight.py`, `publication_staging.py` | Reject archived selection/new staging even when historical package proof still exists. |
| `staging_runtime.py` | Audit direct material locks and late outcomes; an in-flight owner must prevent archive. Preserve fact recording after credential revocation. |
| `api/packaging_downloads.py` | Explicitly permit existing authorized accepted-proof downloads on archives, including its streaming reauthorization guard; never reinterpret current approvals as necessary for historical proof. |
| `api/publication_batches.py`, staging history | Keep immutable old exports/progress readable under existing publication permissions. New execution still checks current archive state. |
| `ai_service_access.py` | Shared human visibility/assignment checks must also reject new archived-material service operations. Never add an archive bypass to a credential. |
| `api/catalog.py` and brand invalidations | Define how catalog edits invalidate archived material review without deleting proof or restoring it on unarchive. |
| Ordinary resource history | ADMIN historical reads remain available; lifecycle actions have separate explicit events, not invented ordinary PATCH snapshots. |

Product detail to preserve: publication states distinguish uploaded/import-pending
from verified publication. The first archive implementation should accept only
`NOT_PUBLISHED` with `is_published=false`, unless a later explicit contract resolves
all external side effects. Reject an ambiguous/in-progress publication state even
if its local published flag is false. Restore resets review readiness, not historical
status or external cloud state. No archive code has been enabled by this plan.
