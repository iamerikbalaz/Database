# AI publication draft delivery plan

Source contract: `docs/codex-handoff-prompt.md`, sections 15 and 17. This is the
next slice after historical import and read-only folder discovery. It is not
implemented or live-verified yet.

## Conservative product decisions

- An AI proposal is separate from saved publication content. It starts AI_DRAFT
  and cannot approve content, change credits/categories/collections, mark Done,
  or publish. A human explicitly compares and adopts/edits a proposal; normal
  revision-bound content approval remains required before publication.
- Publishing context exposes only the material UUID, brand/name, current selected
  categories/collections and explicitly approved source URLs. No raw metadata,
  filesystem paths, contacts, account information or database credentials belong
  in that context. No URL is inferred from a company/project field.
- Source URLs are explicitly approved and audited by administrators. Start with
  an empty list. Only bounded HTTPS URLs without credentials, query secrets or
  fragments are accepted; the server does not fetch them in this slice.
- Service access must use a separate short-lived credential restricted to one
  material and two operations: read publishing context and submit content drafts.
  Human session cookies are never repurposed as an AI service credential. The
  issuing user's current access, material assignment and credential revocation
  must be checked on every call. Administrative credential creation/revocation
  uses existing real sessions, CSRF and audit controls.
- Store the exact context digest, original proposal, source URL references,
  provider/model/prompt version, submitting actor/service identity, timestamp and
  idempotency key. Reject changed context and unrelated URLs. Immutable provenance
  must survive later content edits; API errors/logs do not echo original text,
  arbitrary keys, credentials or model diagnostics.
- External generation is disabled unless explicitly configured. An unavailable
  provider must produce a real failure, never a synthetic successful draft.
  Contract tests may inject synthetic responses. Live generation requires a
  separately configured test provider/credential; none is assumed here.

## Small implementation sequence

1. Add migration 0013 for immutable source approvals/draft provenance and scoped
   credential records; preserve migrations through 0012. Add bounded publication
   context and draft intake with exact replay and database concurrency tests.
2. Add explicit human comparison/adoption and retained-data UI tests. Preserve
   credits and vocabulary selections when applying description/tags, detect stale
   content, and invalidate prior decisions through the existing content workflow.
3. Add provider configuration and an adapter with strict request/response bounds,
   no arbitrary destination URLs, no secret-bearing logs and contract tests. Record
   the actual provider/model/version on the persisted proposal. Never imply live
   provider verification from mocks.

After this slice: immutable publication jobs, exact nine-column CSV and isolated
packaging, then GCS/Notion contracts and the remaining operational/audit backlog.
