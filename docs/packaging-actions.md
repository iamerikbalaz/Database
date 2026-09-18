# Explicit packaging actions

The reservation API now has explicit POST /{execution_id}/run, /retry, /reconcile
and /close actions. All accept idempotency_key, expected_last_dispatch_id and a
nonempty reason. Existing session, current-account, role, forced-password and CSRF
checks apply. ADMIN/LEADERSHIP can run/retry/reconcile; only ADMIN can close.
Packaging remains opt-in and disabled by default.

An exact actor/key replay returns the known current progress without source IO or
another worker command. Changed payloads or execution IDs reject key reuse. A new
action must identify the current last dispatch. Run requires an unsent RESERVED
execution. Retry requires a known RETRY_REQUIRED outcome and no previous CLOSE
intent. Reconcile requires an earlier dispatch. Terminal executions are immutable.

## Execution and recovery

The backend holds its dedicated PostgreSQL session lease across the request, but
each domain transaction is short. Before new execution/retry it checks the exact
approved batch and saved policy under the account/material/folder/brand locks,
reads a fresh source inventory outside the transaction, then reauthorizes and
checks the frozen inputs again. The read does not replace approved inventory or
technical-check records. Only this already-verified execution is excluded from
its own ownership gate; other packaging and identity operations remain blockers.

Before contacting the worker it commits an immutable sequential dispatch and
RUNNING progress together. It sends only the ordered worker command defined in
packaging-dispatch.md. The worker call and all source IO take place outside DB
transactions. A lost response leaves RECOVERY_REQUIRED ownership; the backend
does not retry automatically or infer cancellation from a timeout.

Every verified worker result or fixed transport failure becomes an immutable
observation. The application independently validates the entire proof and exact
command even when a different client implementation is injected. Serialization
warnings cannot echo malformed evidence, and evidence is bounded for PostgreSQL's
stored JSON representation as well as the HTTP wire limit.

READY with an OPEN worker journal becomes PACKAGED only after a fresh matching
source observation, unchanged approvals/policy and a current authorized account,
under the same locks, with the dispatch lease still owned. Revocation returns
401/403 to the original caller after preserving the factual observation. A new
authorized operator can reconcile it. Source/approval/lease uncertainty preserves
RECOVERY_REQUIRED and retained proof. A returning old command may append its
observation but cannot replace a newer dispatch's progress, including a terminal
result accepted while the old backend process had lost its lease.

RETRY_REQUIRED holds ownership until explicit retry or closure. Recovery of
incomplete work and closure do not require NAS access. Recovering a READY proof
with NAS offline preserves it but cannot accept PACKAGED until current source
bytes can be checked. Acceptance is a recorded point-in-time check; it cannot
prevent later changes outside the application and does not authorize publication.

## Closure and history

An unsent RESERVED job still closes locally with CLOSE/NOT_STARTED, including
when packaging is disabled. After any earlier dispatch, closure requires the
worker's verified CLOSED response with READY or RETRY_REQUIRED. It never substitutes
NOT_STARTED for an uncertain call. A lost CLOSE response preserves ownership and
its recorded close intent permanently bars retries. CLOSING/CLOSED observations
from reconciliation require an explicit admin CLOSE before releasing ownership.
Completed retained artifacts are preserved; closure is not artifact deletion.

GET /{execution_id}/dispatches provides ascending ordinal history with after and
limit (1–50). It exposes actor/reason, outcome, current-input/account flags, attempt,
terminal intent, proof hash and fixed failure code, without full proofs, raw source
content, folder paths, credentials or auth-session IDs. Execution detail adds the
latest current-input/account flags and worker terminal state. Audit events record
dispatch and observation facts, including fixed acceptance failure reasons.

No migration is changed. Schema 0017 remains the current immutable head. There is
no production deployment, live upload, publication-status change or online-import
confirmation. Operator UI, proof-bound downloads and actual end-to-end application
HTTP/worker/browser coverage remain the next slices. Unit tests use explicitly
synthetic rebound proof shapes; their success is not a claim of actual ZIP creation.
Actual worker conversion/restart proofs are separately tested and exported.
