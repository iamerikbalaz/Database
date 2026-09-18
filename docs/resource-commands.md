# Recovering ordinary record saves

Migration `20260918_0024` adds successful command receipts for the ordinary
company, published-brand, project, internal-user profile and material POST/PATCH
routes. It does not alter older migrations or backfill invented receipts.

## HTTP contract

Send a client-generated nonzero UUID in `Idempotency-Key`. A successful response
has the existing record JSON and status (201 create, 200 update), with
`Idempotency-Replayed: false` for the original and `true` for an exact retry.
The application record, audit event, material-number reservation where relevant,
and receipt commit atomically. Failed transactions retain none of them.

The key namespace belongs to the current actor, across all ten routes. With the
same actor/key, the same operation, target and submitted JSON return the original
response without performing another write. Changed input conflicts with 409
`RESOURCE_COMMAND_KEY_REUSED`. Later edits do not change the original receipt;
read the normal resource endpoint for current values. A replay never overwrites
those later edits or allocates another material number.

The request SHA-256 covers sorted compact ASCII JSON of
`{schema_version: 1, kind, action, target_id, payload}`. `target_id` is a canonical
lowercase UUID for updates and null for creates. Payload is the submitted JSON
before schema defaults, trimming or URL normalization; key order and whitespace
between JSON tokens are insignificant, but different string values, omitted
fields and explicit defaults are different requests. Only the digest is stored.
The browser and Python implementation share a Unicode/normalization test vector.

`GET /api/resource-commands/{request_key}` explicitly reads the authenticated
actor's successful receipt. It returns actor/key, operation, resource ID, request
and response digests, the original typed response and receipt time. Missing keys
return 404 `RESOURCE_COMMAND_NOT_FOUND`. **An absent receipt does not prove an
earlier request cannot still commit.** Invalid stored responses return bounded
503 `RESOURCE_COMMAND_UNAVAILABLE`; there is no inferred success.

All calls retain current session, forced-password-change, role and CSRF checks.
Reads/replays recheck current role and, for materials, assignment and archive
visibility. A receipt never grants lasting access. Actor credential locks serialize
same-actor requests across different sessions; the existing global access gate
prevents concurrent account demotion/reset from racing an authorized write.

## Browser behavior

The company/brand/project/material forms and account-profile administration forms
freeze the submitted values and key while a save is pending or uncertain. After a network failure they offer
**Check saved result** (GET only) and **Retry exact save** (same input/key).
Read recovery checks the expected actor, key, operation, target, request digest,
response schema and target ID before opening the current record.

Pending requests survive in-app navigation for the same account. Another ordinary
form directs that actor back to the pending form. Delayed completion never
navigates an unrelated page or another account; the original form can open its
saved result when remounted. There is no automatic resubmission. A definite first
4xx rejection permits correction with a fresh key; a rejection after uncertainty
does not erase the original packet.

The profile screen identifies pending work by kind, action and target, so its
create form and several account rows can share `/settings/users` without sharing
one another's submitted values. A page-level link returns to the pending profile
or other ordinary form. Saving/recovering a profile refreshes current account
data; a historical response never replaces a later role/active-state edit.
Initial account reads and delayed completions are scoped to the acting account.
Creation still defaults to PROCESSOR, and self-demotion/disable remains blocked.
Temporary-access forms are hidden while an ordinary save is unresolved; their
secret values never enter the ordinary command controller.

Pending data live only in page memory, never local/session storage. A browser
unload warning remains active while any packet is retained, but users can still
leave: full reload, browser close or crash can lose local recovery data. The
immutable server receipt remains available to its authorized actor with its key.
This is not durable offline queuing or multi-tab synchronization.

## Compatibility and scope

The header is optional for legacy integrations. Keyless requests retain their
previous semantics and are **not safe to retry automatically**. All current ordinary
record and account-profile forms send keys. Password issuance/reset
uses its separate security workflow; passwords are never put in this ledger or
pending ordinary-form packets. Specialized workflow/content/publication commands
keep their own established recovery contracts.

This change does not add optimistic version checks to ordinary edits. Two
intentional, separately keyed edits retain existing field-update semantics.

## Storage and rollback

Receipts use exact resource foreign keys with RESTRICT deletion, an actor/key
unique constraint, whitelisted response keys and digests. PostgreSQL verifies the
response matches the actual record when inserted (timestamps are compared as
instants), rejects extra/missing fields and enforces UPDATE/DELETE/TRUNCATE
immutability. Application reads also validate response shape and digest. Receipt
snapshots contain the same approved profile/catalog fields as the resource API;
they are subject to database access/backup policy and must not be copied to logs.

0024 is additive. It permits downgrade to 0023 only while its table is empty.
Once receipts exist, downgrade refuses their loss; use a reviewed forward
migration. Rolling back application code alone can leave the added table intact,
but an old keyless frontend loses this recovery behavior. Retain backups and do
not bypass the ledger guard to force a downgrade.

## Verification

Tests cover all five resource kinds, exact and conflicting retries, independent
actor namespaces, current authority, atomic rollback, Unicode request binding,
concurrent real PostgreSQL sessions, number allocation, schema/ledger guards,
0023 upgrade and refused destructive downgrade. Browser tests exercise a response
lost after a real committed material create, explicit recovery without a second
write, unchanged later edits and replay after application restart. See the latest
[checkpoint](autonomous-pbr-progress.md) for executed results and limitations.
