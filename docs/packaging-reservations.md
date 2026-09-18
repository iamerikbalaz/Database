# Approved packaging reservations

Application routes live under /api/materials/{material_id}/packaging-executions.
They use the existing real-session, forced-password and CSRF checks. ADMIN and
LEADERSHIP may reserve and read; only ADMIN may close an unsent reservation.

POST the collection with idempotency_key, batch_id, expected_snapshot_hash,
expected_policy_id and a reason. The selected immutable batch item must match the
current complete publication candidate exactly, including the observed inventory,
technical check and approvals. A changed input requires a new approved batch.
The latest saved ZIP-policy decision is required; preparation never silently
selects a policy or changes its timezone.

The backend reconstructs the technical report from immutable records, verifies
their material/generation/revision/hash bindings and asks the separate worker for
a pure plan outside a DB transaction. It then reauthorizes the account and repeats
the current-input checks under the established material/folder/brand locks before
committing immutable inputs and RESERVED ownership together. Planning performs no
filesystem write, conversion, upload or actual publication.

An identical request by the same actor returns the original execution and current
progress without another worker call, even if processing was later disabled.
Reusing the key with changed parameters fails. A different reservation cannot
claim a material or overlapping source folder already owned by an active job.
GET the collection for bounded cursor history or GET /{execution_id} for the
current compact detail. Responses omit raw reports, source content, folder paths,
credentials and session identifiers; historical CSV remains independently readable.

POST /{execution_id}/close with idempotency_key, expected_last_dispatch_id and a
reason. Local unsent closure requires RESERVED with no prior dispatch.
The backend takes its execution lease, records immutable CLOSE/NOT_STARTED facts,
commits REJECTED and releases material ownership. Exact closure replay adds no
second event. Processing may be disabled during this safe closure.

An execution with any prior worker dispatch cannot be closed as NOT_STARTED.
The explicit action API obtains a fenced known worker outcome as described in
packaging-actions.md. An unknown network outcome never proves failure.

## Configuration and remaining work

PACKAGING_ENABLED defaults to false. Opt-in needs a separate redacted
PACKAGING_SERVICE_TOKEN, a private HTTP(S) PACKAGING_BASE_URL origin, and bounded
PACKAGING_TIMEOUT_SECONDS (default 3630, maximum 3660). Compose passes these only
to the backend; the private packaging runtime is configured separately as
described in packaging-service.md. Enabling packaging does not enable NAS identity
mutations. Explicit conversion/recovery actions are documented in packaging-actions.md.
Artifact downloads, publication, worker deployment and operator UI remain separate work.

Tests cover real authorization/CSRF, stale inputs and policy, preparation failure
or response substitution, actual account changes, replay, ownership gates, safe
closure and bounded scoped history. PostgreSQL cases exercise real competing
reservations, account/content/policy/technical changes during preparation, final
commit serialization and competing closure. Results are in autonomous-pbr-progress.md.
