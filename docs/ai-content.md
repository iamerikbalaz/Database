# AI context and immutable proposal intake

This delivery slice adds human-authenticated source approvals, minimal publishing
context and storage of externally prepared AI proposals. It does not contact an
AI provider, fetch a source URL, grant a service credential or publish content.
Explicit human adoption and its UI have been added and are undergoing the complete
validation below. Service access/provider integration remain subsequent steps.

## Context and authorization

`GET /api/materials/{id}/publishing-context` returns the material UUID/name,
brand UUID/name, active selected categories/collections and explicitly approved
source URLs. The envelope includes a context digest and saved-content revision.
It excludes the technical filesystem identity, paths, raw metadata, current
description/tags/credits, contacts, project/assignment data and credentials.
The private digest also binds material/brand/catalog/source changes and review
generation. Restoring public values does not restore a superseded context.

All current routes use the existing real human session and material visibility:
processors see their assigned materials; leads/admins and leadership retain their
existing read rights. Draft submission uses normal material-editor rights.
Only administrators approve or retire source URLs. Writes require CSRF and trusted
Origin, and run with renewed access plus a material lock. Active identity operations
block source changes, context generation and new drafts.

Do not give a human session cookie to an AI client. Restricted service access is
not implemented in this slice. Provider/model labels in externally submitted
proposals are the submitter's declarations, not proof that a provider was called.

## Explicit source approvals

- `GET /api/materials/{id}/content-sources`: complete reference registry.
- `POST /api/materials/{id}/content-sources`: UUID idempotency key, HTTPS URL and
  reason. This is an administrator approval to include the URL in AI context.
- `PATCH /api/materials/{id}/content-sources/{source_id}`: idempotency key,
  expected version, boolean is_active and reason.

Nothing is inferred from company websites or project fields. URLs have a bounded
domain-only HTTPS authority, no user information, query or fragment, default
HTTPS port only, and no literal IP/private local names or hidden controls. Unicode
hosts use IDNA and non-ASCII path characters are percent-encoded. No DNS lookup or
network request occurs. URL approval does not guarantee that its content is true,
safe, publicly reachable or suitable for an eventual external provider.

At most 20 references may be active and 100 references may have existed for one
material. These are conservative bounds; the UI/API must report a limit instead
of returning a silently truncated set. URL identity is immutable. Retire/reactivate
with an expected version; each real change invalidates prior review decisions
and is recorded in material audit. Exact retries and no-op updates do not increment
generation/version again. Normal material/content approval remains independent.

## Proposal contract

`POST /api/materials/{id}/content-drafts` accepts an idempotency key, expected
context digest, provider/model/prompt_version labels, optional plain description,
individual tags, the subset of approved source reference UUIDs actually used,
and a reason. Description is limited to 10,000 characters; tags use the existing
100-item/100-character normalized, deduplicated vocabulary contract. At least a
description or one tag is required. Source URLs from another material, inactive
references and changed context are rejected before any draft is inserted.

The immutable result stores AI_DRAFT provenance: submitting actor, context/digest,
saved-content revision, declared provider/model/prompt version, original proposal,
used source IDs, reason and time. Intake does not write MaterialContent, invalidate
existing approvals or treat the proposal as reviewed. Replaying the same actor/key
and exact payload returns the original result, even after context later changes.
Reusing a key for different input fails.

`GET /api/materials/{id}/content-drafts?limit=20&after=UUID` returns draft history,
with whether each proposal's context still matches. Maximum page size is 50;
database time/UUID ordering handles ties. Invalid inputs never echo proposal text,
URLs, arbitrary property names or decoder details in validation errors.

## Migration and verification checkpoint

New migration 0013 adds material_source_links and material_ai_drafts, their indexes,
constraints and PostgreSQL protection triggers. Existing migrations through 0012
are unchanged. Source reference identity and AI proposals cannot be rewritten or
deleted through ORM or PostgreSQL. Downgrade refuses once either table contains
provenance; keep the additive schema and use a forward migration. No production
migration has been run.

Full isolated project `reawote-test-0f2a26665ab042599d76456b956b596e` passed the initial
context/intake slice: 917 backend, 144 actual PostgreSQL (auth gate 27/27), 354 Linux
worker and 352 frontend tests, lint/build, without skips. Actual PostgreSQL checks
cover concurrent identical/distinct draft requests, source retirement versus
intake, duplicate URLs and active-source limits, immutable provenance, fresh and
prior-0012 upgrade, current/heads/check and protected downgrade. Owned resources
were cleaned and the exact owned database volume retained.

## Explicit human adoption (follow-on changes under verification)

`POST /api/materials/{id}/content-drafts/{draft_id}/adopt` takes an idempotency key,
expected saved-content revision/context digest, reviewed description/tags and a
reason. It requires material-editor access, locks the material and rejects stale
context or a proposal belonging to another material. The server preserves current
credits/categories/collections. It creates a content revision with the original
AI draft's immutable provenance and invalidates earlier approvals. Even adopting
identical wording creates a revision to record provenance. No proposal approves
itself. The existing explicit content decision is still required.

Ordinary content saves keep their previous request hash contract. Later manual
edits preserve AI ancestry in immutable content history and indicate whether the
proposed wording/tags were edited. Existing manual content without AI ancestry has
the same snapshot shape as before, so the new code does not invalidate unrelated
approval digests merely because it was deployed. Exact adoption retries return
the original result even after later content edits.

The initial full Docker image above predates adoption. The newer shared-save and
adoption changes passed 86 focused backend tests and 26 focused frontend content
tests. The AI transport/workspace passed another 29 focused frontend tests plus
lint/build and the E2E TypeScript check. Full Docker run
`reawote-test-2a225f8af0d64015b511fd8b232a62af` passed 927 backend tests, then 145/146
PostgreSQL cases: the second adoption race failed during fixture setup because its
category name collided in the shared test catalog. Each adoption fixture now owns
unique catalog values; the uniqueness rule and concurrency assertions are unchanged.
The complete rerun `reawote-test-f14624c38ab541a798e729a306003b6f` passed 927 backend,
146 actual PostgreSQL (27/27 auth gate), 354 Linux worker and 382 frontend tests,
lint/build, without skips. The migration checks include fresh/prior upgrade,
current/heads/check and populated-downgrade refusal. Owned resources were cleaned.

## Human workspace

Material detail exposes an explicitly loaded AI workspace; opening a material does
not fetch AI data or contact a provider. Administrators manage the bounded source
registry with a reason. Material editors may record an externally prepared proposal,
its declared provider/model/prompt version, and the approved URLs used. Leadership
can read provenance. Source/proposal writes are independent of adoption.

History is paginated, and a proposal with a stale context is readable but cannot be
selected for adoption. The comparison shows currently saved content beside editable
proposed description/tags. The human must enter a reason and explicitly acknowledge
the replacement and discard of unsaved edits elsewhere on the page. Editing text
or tags clears this acknowledgement. Adoption reloads content and approval state.
An unknown write outcome freezes further changes and retains the exact request/key
for retry. Definite rejection leaves the form available for correction or reload.

The fresh/retained E2E scenario exercises source approval, separate proposal intake,
edited adoption, approval invalidation, immutable originals and reapproval. Its first
attempt correctly blocked a fixture lacking the required online category. The fixture
now includes an independently named category, and the earlier catalog test checks
its own four audit records rather than assuming it owns the entire shared catalog.
Rerun and screenshot inspection are pending. No scoped service access, generation
adapter or live provider verification is claimed by this checkpoint.
