# Proof-bound retained artifact transfer

The private packaging service adds POST `/internal/packaging/artifact`. It requires
the existing private service credential and a strict JSON selection: operation UUID,
request hash, plan hash, retained proof hash and the exact file path, byte size and
SHA-256 from that proof. It accepts only a retained READY file. Range requests and
unknown fields are rejected. There is no path-only file endpoint.

Before sending binary headers the worker opens the private retention journal under
its existing nonblocking operation lock, verifies all selection bindings, opens the
allowlisted file without following links and checks its complete size and SHA-256.
NAS inputs and application approvals are not reread. This is historical artifact
access, including after closure; it is not current publication approval.

The response is an attachment with `application/octet-stream`, exact Content-Length,
proof/file hash headers, no-store and nosniff. The transport retains at most two
64 KiB blocks, rehashes bytes during transfer and withholds the final block until
the final size/hash and descriptor/path identity checks pass. It shares the service's
single request slot, holds the retention lock through file reading and releases both
on client disconnect, send failure, cancellation or error. Verification is bounded
to 120 seconds and the complete transfer to 600 seconds. A failure after headers
interrupts the response; it cannot be converted to a successful JSON result.

No temporary full-file copy is created. Corrupt and unknown files are preserved.
There is no artifact deletion, journal rewrite, new packaging attempt or source
mutation. Multi-gigabyte throughput and production storage behavior remain untested.

## Verification

`scripts/test-packaging.ps1` runs the full worker suite with the required real Linux
converter. Download tests exercise actual retained files, restart, offline NAS,
ordered closure, wrong bindings, missing/corrupt/symlink files, bounded blocks and
real descriptor/lease cleanup after interrupted ASGI transfers. These tests use
synthetic data only.

`scripts/test-packaging-service.ps1` builds the production runtime target and runs
actual Uvicorn/HTTP conversion, multi-resolution/16-bit packaging, two restarts,
ordered recovery/closure and streamed download hash checks with NAS offline. It
uses a fresh owned namespace, no network, no host ports or database, a read-only
root and a bounded tmpfs. Only the synthetic smoke script is mounted read-only;
no package fixture is exported. The container is removed and its image retained.

## Application access

The application exposes a bounded GET `/{execution_id}/artifacts` list under the
material's packaging-executions route. Each entry contains a path-derived SHA-256
file ID, display path, size and file hash. GET `/{execution_id}/artifacts/{file_id}`
also requires the exact `proof_sha256` query parameter. Paths cannot be supplied
directly to this public download endpoint. Only accepted PACKAGED jobs are exposed;
unfinished/rejected executions remain unavailable here, even if the private worker
retains some output. ADMIN and LEADERSHIP are the conservative download roles.

Each read reconstructs the immutable approved report and independently verifies
the saved worker request, dispatch, retained proof and file layout. Later content
or policy edits do not rewrite these historical bytes or require NAS access.
The async backend transport rejects redirects, compression, substituted hash/size
headers, truncated/corrupt bodies and oversized errors. It streams bounded blocks,
withholds the final block until digest completion and closes its upstream connection
under a shield even during active cancellation. Its complete lifecycle is bounded
to 610 seconds (or the configured lower worker timeout), without automatic retries.

Current account/session/role checks happen before source IO, after opening the
worker response, on the next available block after one second or 8 MiB, and before sending
the last buffered block. Transactions are short and do not span network IO. A
revocation during streaming interrupts the response; bytes already delivered cannot
be recalled. The application also verifies streamed bytes independently of an
injected transport implementation. Errors before headers return fixed JSON; errors
after headers interrupt the transfer. Downloads do not create packaging attempts,
release another operation's ownership or change publication state.

The completed-job UI loads file pages explicitly and validates their execution,
proof, path-derived IDs and bounds. Downloads use same-origin browser-managed
attachments, keeping large ZIPs out of frontend JavaScript memory. Browser download
progress determines completion; the UI does not infer success from clicking a link.
Ranges/resumption are not supported in this first implementation.
