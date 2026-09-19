# Durable local-copy retirement evidence

Additive migration `20260919_0025` follows immutable `20260918_0024`. It introduces
three append-only tables without changing existing PACKAGED executions or accepted
worker observations:

- `material_packaging_retirements`: one permanent intent per accepted execution,
  with material/observation references, actor/session/request key, reason, original
  request/plan/proof, exact worker command digest and accepted file/byte totals.
- `material_packaging_retirement_dispatches`: ordered explicit execution/recovery
  commands, each with its own actor/session/request key/reason and an exact previous
  dispatch reference. Every command uses the same immutable retirement binding.
- `material_packaging_retirement_observations`: one factual result per dispatch,
  either an independently verified REMOVED receipt or a fixed UNCERTAIN code, plus
  whether actor authorization and dispatch ownership still held when observed.

There is no mutable replacement for the original packaging outcome. Removal status
is derived from these facts: any verified REMOVED observation remains conclusive
even when an older uncertain result arrives later. This preserves facts after
account revocation or a lost database dispatch session without granting access to
the revoked caller.

## Database enforcement

Intent insertion locks the material and checks the currently accepted PACKAGED
execution/observation, its proof and manifest totals. Matching composite references
prevent crossing materials, executions or accepted observations. An active staging
owner referencing that execution prevents retirement. New staging items and owner
claims take the same material lock and reject any retirement intent for their copy.
Other existing staging/publication evidence remains immutable.

Dispatch insertion serializes on its intent and requires a contiguous previous
command. A verified removal prevents further dispatch creation. Receipt insertion
requires exactly the immutable intent's operation, hashes, retirement UUID, file
and byte counts; unknown fields and substituted values are rejected. A late
observation remains permissible for its own already-recorded dispatch.

PostgreSQL rejects UPDATE, DELETE and TRUNCATE of all three tables. ORM events also
reject updates/deletes. A populated retirement ledger refuses downgrade; preserve
the evidence and roll forward. Empty downgrade to 0024 is supported and tested.
Applying this schema does not enable the private service or expose an operator
action by itself. The separate [application API](packaging-retirement-api.md)
implements ownership/download gates; UI/browser verification is tracked in
[the integration plan](packaging-retirement-integration-plan.md).

## Verified boundary

The complete isolated PostgreSQL phase passed **459 tests**, including **27/27 auth
tests**, with no skips. It covers fresh Alembic current/heads/check, upgrade from
0024 with accepted history, empty/populated downgrade, immutable evidence, strict
receipt bindings and real competing retirement/staging transactions. Relevant
local backend tests passed **134**, and the test runner's ten isolation cases
passed. Exact runs/images and subsequent application work are tracked in the
[checkpoint](autonomous-pbr-progress.md).

No production database, existing demo data or backup was migrated. Physical
retirement journals still require the compatible worker; older workers must not
replace version-2 retirement journals with their nested former READY records.
