# Private packaging service

The opt-in runtime now exposes preparation, execution and recovery through a
separate HTTP application. Ordinary worker routes/images and the default Compose
stack remain unchanged. Database job ownership, current-account/approval gates,
operator UI, downloads and integrations are still separate unfinished slices.
Do not connect this service directly to browsers: its credential grants internal
filesystem work and does not represent an application user's permission.

## Image and runtime contract

Build the service target:

    docker build --target runtime -f worker/Dockerfile.packaging -t reawote-packaging:runtime worker

Building without a target still creates the test image used by
scripts/test-packaging.ps1. The service target installs only runtime dependencies;
it has no test package requirement. Both targets use the same exact packaged
ImageMagick policy. The ordinary worker image does not gain ImageMagick.

The service runs as UID/GID 65532, one Uvicorn worker on container port 8081, without
access logs or trusted proxy headers. It publishes no host port. Keep it on a
private application network with no external egress. Require a read-only container
root and source mount, all capabilities dropped, no-new-privileges, bounded memory/
CPU/PIDs/tmpfs, and sufficient explicitly budgeted private storage. Run one service
instance per set of roots; its nonblocking request slot permits one packaging
operation at a time. Per-operation Linux leases also fence another process acting
on the same operation, including surviving conversion children.

Configuration (disabled by default):

| Variable | Meaning |
| --- | --- |
| PACKAGING_ENABLED | Must be exactly true, case insensitive, to opt in. |
| PACKAGING_SERVICE_TOKEN | Independent 32–256 printable ASCII characters; inject privately. |
| MATERIALS_ROOT | Absolute read-only source root. |
| PACKAGING_WORKSPACE_ROOT | Existing private workspace root. |
| PACKAGING_ARTIFACT_ROOT | Existing private retained-output root. |
| PACKAGING_JOURNAL_ROOT | Existing private execution-journal root. |
| ZIP_POLICY_TIMEZONE | Keep consistent with inventory/technical workers and backend policy selection. |

The three private roots must be owned by the runtime UID, mode 0700, disjoint from
each other and the source root, and opened without following links. Keep their
volumes/identities across service restarts. Provision these roots explicitly;
the service does not chmod/chown existing storage or silently initialize roots.
Do not use source-mutation credentials, original/demo database volumes or NAS
directories for private working storage.

Startup verifies the actual executable, exact security policy and private roots.
Incomplete settings fail closed; a missing/unsafe runtime stays unavailable.
GET /health returns only service and ready/disabled status (200 or 503), with no
token, root paths or diagnostics. Readiness does not imply that a particular NAS
folder is online or that a material is authorized.

## Protocol

Every internal request requires Authorization: Bearer with the independent service
credential and application/json. Authentication happens before consuming the body.
Requests are limited to 8 MiB, whether Content-Length is present or the body is
streamed, with a 15-second receive deadline. Duplicate credentials/length headers,
malformed lengths, mismatched lengths and unsupported inputs are rejected.
Responses use no-store/nosniff; validation errors contain only fixed error codes.
OpenAPI and interactive documentation routes are disabled.

POST /internal/packaging/prepare accepts:

- operation_id (UUIDv4), parts (portable relative folder components);
- expected_source_revision_hash and expected_technical_report_hash;
- approval_context_hash, explicit saved policy and storage_timezone;
- a complete successful technical report with inventory;
- optional limits (seconds, staged_bytes, generated_bytes, retained_bytes).

It returns request and request_hash. Preparation has no disk reservation or source
IO. The request document includes the derived plan hash and all normalized limits.
The caller must independently bind the returned operation/source/report/context/
policy values and digest before saving it as an immutable execution input.

POST /internal/packaging/execute accepts that exact request and request_hash,
the approved report and optional retry (false by default). The service checks the
canonical digest before execution. This call is synchronous and may run up to
the configured cumulative limit (default 1800 seconds, maximum 3600), plus bounded
cleanup. Configure the internal client accordingly. A timeout/disconnection never
means cancellation and must never trigger a new operation UUID automatically.

POST /internal/packaging/reconcile accepts request and request_hash only. It
performs guarded recovery and cleanup without reading NAS. Existing execution
replay also reconciles rather than starting a new attempt. A RETRY_REQUIRED
result needs fresh backend authorization/approval checks and explicit retry=true.

Results contain version, operation_id, request_hash, status (READY or
RETRY_REQUIRED), attempt and stored. A READY stored result includes plan_hash,
proof_sha256, its retention attempt/history and the complete proof payload.
The backend must validate those digests and bindings before accepting PACKAGED.
No raw production metadata content or file bytes are returned. A later dedicated
artifact endpoint must validate both proof and the exact allowed file.

Unknown/replaced/corrupt storage remains an error requiring review; error responses
do not imply a safe retry or failed disk write. Busy uses 503, absent operation 404,
invalid HTTP/model input 422, and execution conflicts/refusals 409. An unchanged
request after a lost response returns the original verified retained result.

## Verification

The focused service suite initially passed 29 tests, including actual prepare/
execute/reconcile, offline source recovery, lost committed response, independent
credential enforcement, strict requests, concurrency and bounded streamed bodies.

tests/packaging_service_smoke.py runs against the service image without pytest or
httpx. It launches actual Uvicorn over container loopback, generates synthetic
rectangular input, prepares/converts/retains through real HTTP, verifies unchanged
source bytes, restarts the service, and reconciles/replays with the source root
offline. No host ports, network access, database or real NAS is needed.

The production-image HTTP/restart smoke passed. The complete required-runtime
worker suite passed 656 tests in 306.37 seconds, no skips, with two existing
dependency deprecations. The offline runner safety suite passed all 11 cases.
Run identifiers and retained images are recorded in autonomous-pbr-progress.md.
Live GCS/Notion, publication and deployment remain outside this test scope.
