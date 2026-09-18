# Ordinary record writes: recover a lost response

## Original gap and adopted contract

The ordinary company/brand/project/user/material create and PATCH routes authorized
and atomically audited their writes, but did not bind them to a retry key. The shared
record form reported a lost response as a failed save and let the user
submit again. A repeated create can allocate a second material number or create a
duplicate company. A repeated PATCH can overwrite a newer edit. This is a real
gap against the handoff's mutation-key requirement, even though specialized
workflow, content, lifecycle and publication commands already have their own
recovery contracts.

The implementation follows this contract separately from history pagination:

1. Add an immutable, actor/key-unique success ledger in migration 0024. Retain a
   typed, whitelisted original response and its digest, request digest, operation
   and exact resource foreign key. Store neither credentials nor raw request data.
   The record, audit, number reservation and successful command must commit once
   in the same transaction. Existing committed migrations remain unchanged.
2. Accept an optional `Idempotency-Key` UUID header on the ten ordinary POST/PATCH
   routes, preserving current clients while enabling exact recovery. Missing keys
   retain legacy behavior and must be documented explicitly. Migrated record form
   submissions always send a key. No server-generated key can replace a client's
   stable retry identity. A later API compatibility release can require the header
   once all callers have migrated; this slice must not claim keyless calls are safe
   to retry.
3. Reuse the current authorization gate and per-actor credential lock before
   lookup. Same actor/key/input returns the saved response without a second write;
   changed operation/target/input conflicts. Another actor has a separate key
   namespace. Recheck current role and material visibility even on recovery;
   a prior successful command does not grant permanent access. Historical receipt
   data must not masquerade as a current resource read.
4. Add actor-scoped explicit read recovery. Never infer a missing receipt means
   the original request cannot still complete. Preserve unknown outcomes and the
   exact submitted form values/key in memory across in-app navigation; do not put
   account/contact data into browser storage. Show the current record separately
   after success. Definite first rejection allows correction and a fresh command;
   a later rejection after uncertainty must not silently discard the original.
5. Verify duplicate/conflicting concurrent calls with real PostgreSQL, atomic
   rollback and sequence preservation, current authorization, immutable ledgers,
   prior-schema upgrade/downgrade, client packet ownership and lost-response E2E
   on fresh and retained data.

The compatibility choice above keeps existing integrations functional while the
new frontend adopts the contract. Ordinary edits still use their existing field
semantics; this does not introduce optimistic locking or resolve two intentional
concurrent edits. Specialized APIs keep their established ledgers and payloads.

Implementation and the explicit compatibility/rollback boundaries are documented
in [resource commands](resource-commands.md). The separate account-profile UI is
the next caller to migrate; account credential commands keep their own contract.
