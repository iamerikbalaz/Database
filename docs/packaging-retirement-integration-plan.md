# Application retirement integration: next bounded slices

The storage primitive and its verification are tracked in
[retirement](packaging-retirement.md). The opt-in [private service](packaging-service.md)
and independently validating [client](packaging-client.md) are implemented.
Complete the remaining database/application/UI slices below before enabling removal
for an application user.

## Implemented worker boundary and independent client

- Require the private service credential and its existing admission/body/runtime
  gates. Accept the frozen packaging request and digest, retirement UUID and exact
  retained proof hash. Accept no file paths or arbitrary deletion roots.
- Open the recorded execution under its inherited lease and existing root/request
  guards. Require a known READY execution whose saved result binds the exact proof.
  Pass the already-bound artifact root identity into the storage primitive.
- Retirement replay must work without NAS and without reconstructing deleted bytes.
  Keep execution history unchanged; legacy recovery may correctly report a retired
  result after the storage tombstone. It must never start a replacement attempt.
- Validate the compact receipt independently in the backend against its accepted
  request, plan, proof, retirement UUID, file count and total byte count. A fixed
  transport failure remains uncertain; no automatic retry or inferred absence.
- Fence delayed ordinary dispatches before changing execution history, so an old
  CLOSE command cannot strand exact retirement recovery in CLOSING after deletion.

## Additive database evidence and gates

Use a new migration after immutable 0024. Preserve PACKAGED execution/observation
facts. Add separate immutable retirement intent, ordered dispatch and observation
facts, with actor/session, reason, actor-scoped idempotency and matching composite
references. One retirement intent per accepted execution permanently prevents that
copy being selected again. A verified removal result is monotonic; a late uncertain
observation must not undo it. Exact later worker replays return the same receipt.

Before committing intent, lock the material and target execution and verify the
accepted observation/proof. Require no active staging owner referencing that
execution. Serialize this test against new staging reservations, and add PostgreSQL
guards against direct incompatible claims. Retirement does not require fresh NAS
bytes or current publication approvals: it removes a historical local copy and
never modifies the source. It must not erase any staging or publication evidence.

A committed intent blocks new download/selection requests even while the worker
outcome is uncertain. A reader already holding the worker retention lock can
finish; physical removal must receive BUSY instead of racing it. New upload
reservations cannot use the quarantined execution. A different newly generated
package uses a new operation UUID and its normal current-approval checks.

## Explicit operator action and recovery

Conservative role: ADMIN only. Require current session/account/role, CSRF, reason,
exact accepted-proof acknowledgment and request key. Keep retirement disabled by
default until explicitly configured. Retire one accepted execution at a time;
there is no time-based or batch-wide deletion shortcut in the initial operation.

Commit dispatch intent before worker IO. Hold the execution's dedicated session
lease while keeping database transactions short. Persist an immutable factual
observation even if authorization or lease ownership changes during IO, but do not
grant the revoked caller fresh access. Another current administrator must be able
to explicitly recover the same retirement intent with an audited action, without
needing the original administrator's session or idempotency packet.

The UI shows the proof/history and a reviewed removal action. Explain that the
local copy becomes unavailable and future use requires a newly generated package;
source files and historical evidence remain. Unknown responses preserve exact
request bindings, offer explicit recovery and never imply completed removal.
Historical download controls become unavailable after intent, with retirement
state and audit visible to authorized operators.

## Verification and rollout boundary

Require strict worker HTTP/client contracts; actual HTTP restart/lost-response
smoke; backend role/CSRF/revocation and delayed-result tests; real PostgreSQL fresh/
prior upgrade, Alembic parity, append-only/downgrade and concurrent claim gates;
frontend component checks; fresh and retained browser scenarios using owned
synthetic packaging storage only. Inspect changed desktop/mobile layouts.

Populated retirement evidence must refuse destructive downgrade. Older workers
reject version-2 retirement journals; preserve them and roll forward. Keep the
feature disabled during any incompatible mixed-version rollback. Automatic cleanup
after terminal staging/import should later call this same audited operation once
its applicable terminal-state contract is verified. Remote object deletion and
actual online importer verification are outside this local operation.
