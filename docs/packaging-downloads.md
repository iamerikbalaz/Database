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

Application download authorization, the independently validating backend transport
and operator download UI are the next integration slice. This private endpoint
alone does not grant end-user access or mark a material as uploaded/published.
