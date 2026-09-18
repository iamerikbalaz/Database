# Ordered worker commands and permanent closure

A live process lock prevents overlapping IO but cannot reject an HTTP retry that
arrives after a newer backend request has finished. Application dispatch therefore
uses a durable worker command fence in addition to database ownership and both
process/session leases.

POST /internal/packaging/dispatch accepts the frozen request and request_hash plus
dispatch containing a version-4 UUID id, positive PostgreSQL-integer ordinal and
EXECUTE, RETRY, RECONCILE or CLOSE action. EXECUTE is ordinal 1; RETRY and RECONCILE
require a later ordinal. The approved cached report is required only for execution
actions and is forbidden for recovery/closure. The private token, body/runtime
limits and one-worker slot apply exactly as on the existing private API.

Under the inherited Linux execution lease, the worker persists the latest command
before doing any work. Lower ordinals are stale. The same ordinal with changed
identity/action conflicts. Exact replay only reconciles known work; an interrupted
retry does not silently start another attempt. A higher RETRY is a new explicit
attempt and still uses the unchanged source/plan checks and byte/time limits.

The execution journal upgrades from version 1 to version 2 when an ordered command
first arrives, preserving its request, root identities, attempt history and proof.
Version 2 adds the command and OPEN/CLOSING/CLOSED terminal intent. Unordered
execute/reconcile entry points reject version-2 records with
PACKAGING_DISPATCH_REQUIRED; old entry points cannot bypass a fence. Legacy
version-1 low-level paths remain for existing internal consumers, but application
jobs must use the ordered client method exclusively.

CLOSE persists CLOSING before recovery and permits CLOSED only after verified
recovery/cleanup returns a known READY or RETRY_REQUIRED result. Both CLOSING and
CLOSED permanently prohibit further conversion, including higher-ordinal retries.
A failure during closure preserves CLOSING and ownership for another explicit
reconciliation/closure. Closing never deletes verified retained artifacts.

RECONCILE/CLOSE of an absent execution creates a private known first reservation
and recovers it to RETRY_REQUIRED without reading NAS or producing an artifact.
This durably rejects a delayed initial EXECUTE and handles the DB-commit/HTTP-send
gap. Unknown existing directories, changed roots or unproven cleanup ownership
remain errors and are preserved. An empty fenced attempt is real journal progress,
not a conversion-success claim.

Responses carry the unchanged verified packaging result plus the exact dispatch
and terminal state. The backend independently validates those fields as well as
the full request/report/layout/proof contract. CLOSE is accepted only with CLOSED;
execution actions require OPEN. Recovery can report OPEN, CLOSING or CLOSED so the
database can choose an appropriate later operator action. The backend transport
never automatically retries an uncertain request.

## Verification and compatibility

Linux tests cover delayed initial/retry requests, conflicting command identities,
exact failed-retry replay, NAS-offline reconciliation/closure, legacy bypass,
cleanup failure with durable closing intent, actual retained proof equality and
three actual child-process deaths around command/closure persistence. Real HTTP
tests exercise the new boundary and terminal proof. The production-image smoke
supports a fourth CLI argument, ordered, and can export result/recovered/closed
envelopes after actual conversion and two service restarts.

packaging-dispatch-contract.json is a synthetic export of that smoke with actual
2K/1K and 16-bit conversions/current timestamps. It has no token, raw production
metadata or absolute host paths. Its three envelopes are independently validated
by backend tests; earlier four immutable proof fixtures remain unchanged.

An older worker cannot read version-2 journals. Preserve those journals and
artifacts and roll forward; do not downgrade a worker which has accepted ordered
commands. No database migration changes in this slice. Application run/retry/
reconcile and post-dispatch closure are now implemented in packaging-actions.md;
their integration and operator UI have a separate verification boundary.
