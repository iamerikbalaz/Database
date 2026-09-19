# Application retirement integration: implemented contracts

The storage primitive and its verification are tracked in
[retirement](packaging-retirement.md). The opt-in [private service](packaging-service.md)
and independently validating [client](packaging-client.md) are implemented.
The database, [application API](packaging-retirement-api.md) and operator UI are
implemented and verified with fresh/retained synthetic browser scenarios. Removal
remains disabled by default; production deployment/enablement has not occurred.

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

## Implemented additive database evidence and gates

[Migration 0025](packaging-retirement-database.md) preserves PACKAGED execution/
observation facts and adds separate immutable retirement intent, ordered dispatch
and observation facts, with actor/session, reason, actor-scoped idempotency and
matching composite references. Database staging claims reject retired copies.
The application enforces the same exclusion for downloads and derives removal
from any verified receipt, so a late uncertain observation cannot undo it.

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

## Implemented application action and recovery

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

## Implemented operator UI and browser verification

The UI shows the proof/history and a reviewed removal action. It explains that the
local copy becomes unavailable and future use requires a newly generated package;
source files and historical evidence remain. Unknown responses preserve exact
request bindings, offer explicit recovery and never imply completed removal.
Historical download controls become unavailable after intent, with retirement
state and audit visible to authorized operators. Component/client tests and all
23 fresh plus 23 retained real-browser scenarios passed. A second real accepted
copy is removed while the original remains downloadable across restart. Lost
committed replies recover by reading evidence; desktop/mobile layouts were inspected.
Exact run and image identities are in the [checkpoint](autonomous-pbr-progress.md).

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
