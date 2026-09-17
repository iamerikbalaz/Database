# Restricted AI service API

The service accepts only context reads and proposal submissions for one material.
It does not fetch URLs, generate text, adopt or approve content, expose other domain
records or publish. The API and administrator credential management UI are verified.
A real generation adapter remains unfinished.

## Administrator workspace

Open **Manage AI service access** on the material detail page. Give a reason and
choose 15, 30 or 60 minutes. Issuance displays a masked one-time credential; copy
it to the intended client and hide it. It is held only in page memory. History
shows metadata and permanent revocation controls. Leaving the material or losing
administrator access unmounts that memory. An unknown mutation outcome freezes
controls and offers only the exact same request retry; issuance replay cannot
recover a lost secret. Revocation leaves proposal history available for review.

## Human administrator operations

Use a real administrator session, trusted Origin and its CSRF header:

- `POST /api/materials/{id}/ai-service-credentials`: UUID idempotency key, reason,
  optional `lifetime_seconds` (default 900; integer 60–3600). At most ten unexpired,
  unrevoked credentials per material. Invalid issuer sessions still count until
  their service credentials expire or are explicitly revoked.
- `GET /api/materials/{id}/ai-service-credentials?limit=20&after=UUID`: bounded
  history, at most fifty rows/page, with credential/actor/material UUIDs, dates,
  revocation date and two fixed scopes. No digest, token or human session ID.
- `POST /api/materials/{id}/ai-service-credentials/{credential_id}/revoke`: UUID
  idempotency key and reason. Any administrator may revoke; revocation is permanent.

Successful issuance returns `credential`, `token`, `secret_available: true`. The
token is displayed **once**. Only the 256-bit random secret's SHA-256 digest is
stored. Audit/idempotency storage includes metadata with `token: null` and
`secret_available: false`. Exact issuance retry returns those same credential
metadata without the secret. If the first response was lost, replay to identify
the credential, revoke it and issue another. Never issue repeatedly with fresh
keys while the previous outcome is unresolved. All responses use no-store.

Issuance and revocation do not change content revision, invalidate approvals or
alter material review generation. The original immutable proposal remains after
credential expiry/revocation; authorized humans can still review and adopt it.

## Service operations

Send `Authorization: Bearer <one-time token>` from a separate server/client with
no browser cookies, Origin header or URL query parameters. Use HTTPS for any
non-loopback deployment and keep tokens only in protected client memory/secret
storage. Do not put them in command arguments, shell history, logs or URLs.

- `GET /api/ai/materials/{id}/publishing-context` returns the existing minimal
  UUID/name/brand/categories/collections/approved-URL context and revision digest.
- `POST /api/ai/materials/{id}/content-drafts` accepts the same bounded proposal
  contract as human intake, with exact current digest and approved source IDs.
  It returns only `{id, status: "AI_DRAFT", context_hash}`. It exposes no actor,
  session, saved content, credentials or unrelated proposal history.

Human request UUIDs and each service issuance have distinct replay namespaces.
Payload hashes additionally bind the exact original request and credential UUID.
Replay still requires valid service access. Proposal rows and human adoption history
retain the service credential UUID as immutable provenance. Provider/model names
remain declarations; a service credential proves authorization, not generation.

## Lifecycle and transaction boundary

Each call rechecks issuer account, role, password-change requirement, issuing
session and material assignment. Logout, reset, issuer-session deletion/expiry,
account disable, missing rights, credential expiry or explicit revocation deny
access. A valid issuer session is necessary even if the service's own deadline is
later. Service calls do not extend the issuing session's idle timeout. New login
does not revive a credential tied to a revoked/expired prior session.

Order: shared account gate, issuer password row, issuer session, service credential,
material, context references. Revoke locks its credential before the material.
Service expiry is rechecked with database wall-clock time after material/context
lock waits. Credential authentication and proposal commit share a transaction.
Human session IDs are retained as immutable UUID audit references without a foreign
key that would force retention of expired human session secrets; a missing session
always denies access. No service credential works on ordinary application routes.

## Migration and verification

Additive 0014 creates `ai_service_credentials` and adds nullable
`material_ai_drafts.service_credential_id`. Scope fields cannot change and revocation
cannot be cleared/rewritten; SQL triggers block deletion/truncation. Populated
downgrade refuses to destroy credential provenance. Keep the additive schema and
use a forward migration. Migrations through 0013 are untouched; no production
migration has been run.

Local actual-session tests passed 93 cases across AI/service/adoption/domain auth
and Alembic head. This includes 28 new service tests covering the one-time response,
no secret persistence, authorization on every human domain route, independent keys,
scope, expiry, revocation and issuer lifecycle, source checks, bounds and validation.
One initial test wrongly expected logout HTTP 204; the existing contract returns
200, and the assertion now matches it. No authentication behavior was relaxed.

Full isolated Docker project `reawote-test-ef16ea5d2e5445b3989d3228ff76d91c` passed
955 backend, 155 actual PostgreSQL (auth gate 27/27), 354 Linux worker and 382 frontend
tests, lint/build, without skips. Nine new actual PostgreSQL cases exercise
issuance races, bound enforcement, service replay, revoke versus in-flight submit,
expiry after material-lock waits, immutable scope and prior-0013 upgrade/downgrade.
All passed, including fresh/prior upgrade and Alembic current/heads/check. Final
E2E run `04d680f7-7332-4265-81cf-055fad545e69` passed 16 fresh scenarios (54.0s) and
16 retained scenarios (36.4s), with real service requests, scope rejection and
permanent revoke, followed by browser-visible immutable proposals. Secret material
stayed only in test request memory; no secret was rendered or retained in artifacts.
Owned cleanup and protected-resource checks passed. An additional focused route
inventory regression verifies that precisely the two declared AI routes exist and
reject anonymous and browser-only authentication. No live provider or source URL
was contacted. These image snapshots precede the credential-management UI.

The later UI passed all 399 frontend tests, lint/build and E2E TypeScript checks.
E2E run `1e426634-998a-434c-952f-6d0617f063bc` passed 16 fresh (54.6s) and 16 retained
(36.1s) scenarios, including browser revocation and credential metadata after
restart. Desktop and 390px history screenshots were visually inspected; no
horizontal overflow. One-time test secrets remained outside rendered artifacts.
Owned cleanup and protected-resource checks passed. No tests were skipped.
