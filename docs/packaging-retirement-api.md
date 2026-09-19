# Explicit removal of an accepted local package

This application operation removes one accepted execution's retained local copy.
It preserves the material, source files, original PACKAGED proof, CSV batches,
staging records and publication history. Reuse requires a new packaging execution
with its ordinary current-source and approval checks. It does not delete remote
objects or infer that an online importer has finished.

## Configuration and access

Apply additive schema **0025** before running this application version. Retain the
compatible private packaging service and its journal/artifact volumes. Both the
backend and private service require `PACKAGING_ENABLED=true` and the separate
`PACKAGING_RETIREMENT_ENABLED=true`; retirement defaults to false. A disabled or
unavailable private service produces an uncertain recorded outcome, never assumed
deletion. Deployment and production enablement have not been performed.

ADMIN and LEADERSHIP can read evidence. Only a current ADMIN can request removal
or explicit recovery. Existing account/session, forced-password and CSRF checks
apply on writes and are renewed after private IO. Archived materials and later
content/policy edits do not prevent historical removal. No fresh NAS read or new
publication approval is required.

All routes are under
`/api/materials/{material_id}/packaging-executions/{execution_id}`:

| Route | Contract |
| --- | --- |
| GET `/retirement` | `{enabled, retirement}`; null means no committed intent. |
| POST `/retirement` | UUID `idempotency_key`, `expected_observation_id`, exact `expected_proof_sha256`, `acknowledgement: REMOVE_LOCAL_COPY`, nonempty `reason`. Returns 201 with the recorded view. |
| POST `/retirement/reconcile` | UUID `idempotency_key`, `expected_retirement_id`, exact `expected_proof_sha256`, acknowledged `expected_last_dispatch_id` (nullable only before the first dispatch), the same acknowledgment literal and a new reason. Returns the recorded view. |
| GET `/retirement/dispatches` | Ascending immutable actions/observations; `after` ordinal, `limit` 1–50 (default 20), `next_cursor`. |

A view is RESERVED, RUNNING, RECOVERY_REQUIRED or REMOVED. Only an independently
verified immutable receipt establishes REMOVED. HTTP 201/200 alone does not mean
physical deletion. The view exposes actor, reason, accepted proof, file/byte
counts and last dispatch; it excludes credentials, auth-session IDs, source paths
and raw metadata. History records actor/lease changes and fixed failure codes.

## Transaction, download and staging boundaries

The backend acquires the execution's dedicated PostgreSQL session lease, then
checks the accepted immutable request/report/proof and exact acknowledged
observation under short account/material/intent transactions. An active staging
owner for this execution prevents intent creation. Migration 0025 serializes
competing staging claims on the same material and also guards direct SQL claims.

Intent, first dispatch and audit are committed **before** private IO. Every
committed intent quarantines new artifact listings, downloads and staging
selection/reservation, regardless of the feature flag or uncertain worker result.
Download authorization is checked again before opening the worker. A download
already holding the worker retention lock can finish, with normal current-account
reauthorization throughout the stream. The worker must return BUSY to competing
removal instead of deleting bytes under that reader.

The worker receives the original frozen request/hash, accepted proof and the
intent's retirement UUID. Every recovery sends that same retirement identity.
The backend independently validates all receipt bindings and persists a factual
observation even after actor revocation or loss of its session lease. A revoked
caller still receives 401/403 after that commit. A later current administrator
can read and recover the intent. A late UNCERTAIN observation cannot undo an
already verified REMOVED receipt.

Exact actor/key/body replays read existing progress without another worker call,
including when the feature is subsequently disabled. A changed payload or target
rejects key reuse. A new recovery must acknowledge current progress; concurrent
execution is excluded by the same dedicated lease. There are no automatic
retries, cancellation, force-delete or bulk-delete shortcuts.

## Recovery procedure

1. Read `/retirement` after a timeout, connection loss or account change. Keep
   the same original packet if no intent is visible; absence is not proof that
   an in-flight request cannot still commit.
2. If REMOVED, retain its receipt and history. Do not rebuild deleted bytes or
   modify the original packaging facts.
3. If another state is recorded, check dispatch history and the compatible worker
   configuration. For BUSY, let the active reader/operation finish first.
4. A current ADMIN explicitly acknowledges that intent/proof/latest dispatch and
   submits `/reconcile` with a new action key and reason. This resumes the same
   worker retirement or retrieves its durable receipt.
5. A fixed unsafe/corrupt-history error needs operator investigation of the owned
   storage. Preserve all evidence; do not delete journals to make recovery pass.

## Operator controls

Open Material packaging on material detail or the selected material in a saved
CSV batch, then open its PACKAGED job. The UI reads local-copy availability before
offering file downloads. ADMIN sees the removal form only when the feature is
enabled; reason and explicit acknowledgment of permanent removal are mandatory.
Leadership can inspect history and copy availability but cannot remove it.

An unknown write response freezes its exact packet and offers a read-only check
or explicit same-request retry. There is no automatic resend. A known unfinished
intent can be explicitly recovered with its existing identity and latest dispatch.
Changing actor or selected proof retires late callbacks; verified removal cannot
regress to available. Browser unload warns about a pending packet. After navigation
or a new session, use the stored removal view/history for recovery; no credential
or write packet is saved in localStorage. Downloads use browser-managed streams.

The [UI integration plan](packaging-retirement-integration-plan.md) and
[checkpoint](autonomous-pbr-progress.md) distinguish implementation and verified
runtime evidence. Backend adapter tests use explicitly synthetic receipts; real
filesystem/HTTP restart verification is tracked separately in
[the worker contract](packaging-retirement.md).

## Rollback boundary

Disabling the feature prevents new worker actions but keeps history and download/
staging quarantine. It does not cancel committed intent or restore deleted files.
Preserve schema 0025 and version-2 worker tombstones. A populated ledger refuses
downgrade; old application versions lack the quarantine checks. Prefer a compatible
roll-forward with the feature disabled. Never replace a tombstone with its nested
former READY record or migrate an existing production database as part of tests.
