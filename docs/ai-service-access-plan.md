# Restricted AI service access: next additive slice

The API, migration 0014 and credential management UI are implemented and verified;
see `ai-service-access.md`. This document records their design constraints.
The optional generation client is described in `ai-generation-client.md`; live
provider verification remains outstanding. No human session is passed to an AI client.

## Conservative access contract

- Use dedicated `/api/ai/materials/{id}/publishing-context` and `/content-drafts`
  service routes. No other application route accepts this credential. Reject mixed
  browser-cookie and bearer authentication. A service cannot list users, inspect
  saved content/history, approve source URLs, adopt proposals or publish.
- An administrator with a current real session and CSRF may issue or revoke a
  credential for exactly one material. Default lifetime 15 minutes, maximum one
  hour. Its issuing session, account and password credential must remain valid;
  logout, session expiry, reset, required password change or account deactivation
  must remove access. Any later material assignment restriction also applies.
- Generate 256 random bits on the server. Store only a SHA-256 digest and public
  credential UUID. Return the secret once with no-store; never include it in audit,
  an idempotency result, URL, exception or logging. An exact issuance retry returns
  the same credential metadata with an explicit unrecoverable-secret indication.
  A lost first response is resolved by revoking that credential and issuing another.
- Use the existing account access lock order: account gate, issuer password row,
  issuing session, service credential, then material. Revoke must use the same
  order. Authenticate/recheck and commit a proposal within the same transaction.
  Check expiry using database wall-clock time after locks, not transaction-start
  time. This prevents a blocked request succeeding after expiry or revocation.
- Keep issuance/revocation audit independent of source/content generation. Bound
  active credentials per material, paginate credential history and disclose no
  digest. Reject unknown/revoked/expired/wrong-material credentials uniformly.
- Each service proposal records credential identity in new immutable provenance.
  Human proposals keep their existing shape. Use an additive migration after 0013;
  never mutate migration 0013 after committing it. Separate service request keys
  from human request keys and bind replay to the credential and exact request.
  Replay remains subject to current authentication and revocation.

## Required verification

Actual auth/CSRF tests for administrator issue/revoke; lack of bearer authority on
every ordinary domain route; one-material context allowlist; no secret in database
audit/validation/error bodies; exact replay versus conflicting input; expiry and
all issuer/session changes; concurrent revoke/submit and identical/distinct submit
requests on real PostgreSQL. Cover fresh/prior upgrade, schema check, immutable
credential provenance and guarded downgrade. No provider/network call is needed
to verify these boundaries. Live provider integration is the subsequent slice.
