# Derived packaging storage cleanup

This implements the handoff's cleanup requirement in separate bounded changes.
It does not authorize deleting production files or making external writes. Test
only private synthetic Linux roots. Never remove source SOURCE, PREVIEW, master
maps or root production metadata. Copies in an independently owned private artifact
root are distinct from those protected source files.

## 1. Temporary attempt work

The execution journal already records private workspace ownership before any copy.
Handled errors now invoke the existing guarded cleanup under the execution lease.
Persisted WORKING/RETAINED evidence stays unchanged until explicit reconciliation:
an error after retention cannot establish that no result committed. Unknown files,
replaced roots and ownership gaps remain preserved. Actual process death keeps the
existing crash recovery path. See [execution](packaging-execution.md).

## 2. Incomplete retained copies

Retention cleanup is implemented and verified for a proven incomplete incoming
copy; see the current checkpoint. Hold the
existing retention lock and verify the immutable request/plan, journal, recorded
directory identity, allowed entries and surviving file contents before deletion.
Reuse the existing bounded no-follow cleanup. Preserve the journal and proof hash;
the next explicit authorized retry must regenerate output and retain a new attempt.

A complete incoming copy must finish recovery instead of being deleted. READY
output is preserved. Unknown files, ownership gaps, full-size corrupt files and
changed roots refuse cleanup. Apply a bounded verification budget; an exhausted
budget preserves the unproven tree. A failure must still clean a safely owned
temporary attempt workspace even if retained-copy cleanup needs later recovery.

Test partial copies, complete output followed by a lost return, changed files/roots,
busy downloads, real process death during cleanup, and exact retry history. A
missing incomplete directory is compatible with the existing BUILDING journal;
never reinterpret missing READY output as a safe retry.

## 3. Accepted artifact retirement

Accepted PACKAGED output is currently used by historical downloads and staged
upload selections. Expiring it needs a distinct durable retirement contract, not
an unrecorded filesystem delete. Keep the original request, proof, observations,
dispatch history and accepted database result immutable.

Conservative first application operation: explicit ADMIN retirement with reason,
acknowledgment and actor-scoped idempotency key, after all staging owners referencing
the package are closed. Serialize against new staging claims and packaging actions.
A committed retirement intent must prevent new selections/downloads; an uncertain
worker reply must remain recoverable with the exact original retirement request.
The retention lock must exclude active readers before any byte removal.

The worker needs a durable retirement intent bound to the exact operation,
request/plan/proof hashes and cleanup request. Persist intent before deletion and
support restart after every removal boundary. Verify private root/operation/lock
identities, exact allowed entries and file identities. Preserve ambiguous or changed
trees. A final tombstone must distinguish intentional removal from corruption and
prevent delayed execute/retry/read requests from recreating the old result.

The [internal storage primitive](packaging-retirement.md) is implemented and verified;
it has no exposed retirement endpoint or application authorization in that commit.
Application integration requires additive database provenance/guards, private worker/client contracts,
download and staging gates, operator controls, PostgreSQL race tests, actual Linux
fault tests and browser recovery coverage. That integration is not yet implemented. Automatic
retirement after terminal upload/import can then use the same audited operation;
do not invent a time-based expiry policy or claim the whole cleanup requirement is
complete before this integration exists. Remote staging-object deletion remains
outside this local lifecycle.
