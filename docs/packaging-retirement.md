# Internal retained-copy retirement

`worker/app/packaging_retirement.py` implements the storage primitive for explicit
retirement of an exact READY package. The complete Linux suite passed 780 tests. There is
no HTTP retirement endpoint, application command or operator UI yet. The caller
must first durably authorize retirement and hold execution ownership; a library
argument is not an application permission. The application integration is specified
in [the cleanup plan](packaging-cleanup-plan.md).

## Exact binding and durable evidence

The request supplies the operation UUID, original request and plan hashes,
independently held retained-proof hash, retirement UUID and already verified
artifact-root device/inode identity. It cannot supply arbitrary file paths. The
private retention operation lock excludes an active download or writer. Existing
root/operation/lock checks remain mandatory throughout removal.

Before any removal, the worker verifies the complete READY tree against its saved
proof, reads all file bytes and captures file signatures and directory identities.
Incomplete, missing, corrupt, replaced or unknown output refuses a new intent.
The worker writes a version-2 retirement journal atomically and durably:

- `retained`: the complete original version-1 READY record, unchanged, including
  the original request/plan, proof payload, retained attempt and prior history.
- `retirement`: the exact retirement request digest, physical root/operation binding
  and the captured allowlisted file/directory signatures.
- `status`: REMOVING until byte removal is complete; REMOVED afterward.
- `receipt`: absent during removal, then the exact operation/request/plan/proof and
  retirement bindings plus the original delivered file and byte counts.

The original proof remains evidence of what was produced. The receipt is evidence
that the owned local copy was removed; it is not evidence of upload, import or
publication. A repeated successful request returns the same detached receipt
without rewriting the journal. Different retirement IDs or bindings conflict.

## Removal and interrupted work

After intent, any surviving entry must still belong to the original manifest and
match its captured physical identity and bytes. The worker validates the remaining
tree before removing files, checks each file signature immediately before unlink,
flushes each parent, removes verified empty directories from deepest to shallowest,
then removes the exact READY root and records REMOVED. Paths use anchored no-follow
descriptors. A replaced source path cannot redirect removal outside the owned copy.

An interrupted REMOVING operation can resume only with the original request. Missing
entries are compatible with prior removal after that durable intent. A missing READY
root after the last removal but before the final journal write is recoverable. A
reappearing root after REMOVED is preserved and refused. Unknown/corrupt entries,
changed roots/operation/lock identities and altered journal evidence require review;
there is no force-delete path. Verification/removal is bounded to at most 120 seconds
and the existing proof/tree limits. A timeout keeps the durable intent for recovery.

The storage reader recognizes version-2 retirement journals. Both REMOVING and
REMOVED block legacy retain/recover/download/incomplete-cleanup calls with a fixed
retired error. Delayed old requests cannot recreate the removed operation. The old
operation lock and full journal remain; no SOURCE, source PREVIEW, master maps,
production metadata, remote objects or other operation directories are removed.

## Compatibility and verification

Existing version-1 journals are unchanged until an explicit retirement command.
An older worker rejects the new journal format; preserve it and roll forward.
Do not downgrade or replace it with the nested original READY record, which would
erase the retirement fence and falsely claim that deleted bytes remain available.
No database migration is part of this primitive.

Linux synthetic tests exercise exact receipt/proof preservation, fenced legacy
calls, changed keys/bindings, incomplete/unknown output, active readers, corrupt or
replaced remaining trees, strict journal validation, time limits and real process
death after intent, file/directory removal, READY-root removal and final commit.
See the current [checkpoint](autonomous-pbr-progress.md) for completed results.
Application authorization, immutable database retirement provenance, staging claim
races, private service/client contracts and browser recovery remain next steps.
