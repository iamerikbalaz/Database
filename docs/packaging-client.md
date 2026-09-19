# Backend packaging contract and transport

The private worker is not trusted merely because it returns READY. The new
packaging_contract and packaging_client modules independently bind the wire
request, approved report and retained proof before a database job may accept it.
They are connected to the explicit [application actions](packaging-actions.md).
There is no background scheduler or automatic execution retry.

## Request and response checks

PreparedPackaging verifies the canonical request digest and every requested field:
operation UUID, folder components, source/report/approval hashes, explicit saved
ZIP policy, timezone and limits. The returned plan hash is frozen with those
inputs. The technical report is validated using the existing strict backend
TechnicalReport/SourceInventory schemas and its independent approved digest.

PackagingResult verifies operation/request identity, READY versus RETRY_REQUIRED,
execution/retention attempt numbers/history, plan and all nested proof hashes.
The retained payload has a strictly bounded schema and no unknown fields.

Against the cached approved report, it independently derives the expected effective
master and lower resolutions, integer geometry, source map names and bit depths,
COPY versus RESIZE and the verified-master input chain. Every conversion must
attest the exact reviewed ImageMagick security-policy digest and consistent output
facts. The policy digest is deliberately pinned; a reviewed policy change requires
updating this consumer and its contract fixtures.

It derives the web manifest bytes, then verifies manifest hashes, exact ZIP names
and paths, maps, byte-preserved production metadata and preview hashes, empty
preview directories and the independently retained files. Extra/missing/duplicate
paths, unsafe components, false legacy timestamps, inconsistent geometry/proofs
and output beyond frozen budgets are rejected even when outer hashes are recomputed.
Each archive also respects the current writer's compressed/expanded bounds.

These are protocol and provenance checks. Actual source/ZIP byte verification
occurs in the worker; the backend must still recheck current approvals and source
context before publication, and verify artifact bytes during later transfer.

## Transport

WorkerPackagingClient is disabled unless explicitly enabled with a separate
SecretStr service credential of the configured length/character range. Its base
URL must be an HTTP(S) origin without userinfo, query, fragment or path.
The client uses neither environment proxies nor redirects and performs no
automatic retries. It sends bounded JSON and rejects encoded/non-JSON responses,
oversized or mismatched Content-Length and excessive streamed bytes.

Default request timeout is 3630 seconds (maximum configuration 3660), with a
5-second connection timeout and a cumulative response-read deadline. The execution
request has its own maximum 3600-second work budget. A timeout is an uncertain
outcome requiring reconciliation; it is never proof that disk writes failed.

prepare validates the returned request against the caller's input. execute and
reconcile revalidate the frozen model and cached approved report; reconciliation
sends only the request/digest to the worker, never rereads NAS. Known fixed error
codes are preserved; all other diagnostics, bodies and parser causes are hidden
behind PACKAGING_UNAVAILABLE. No request/credential/body logging is added.

The `retire` client additionally revalidates an independently accepted ordered
READY result, its frozen request and technical report before any IO. It sends only
the request/digest, retirement UUID and proof hash. Its compact receipt must bind
the exact operation, request, plan, proof, retirement key and canonical retirement
request hash, plus the accepted file/byte totals. All fields are strictly typed;
extra fields, altered nested models and plausible but mismatched receipts fail.
Retirement responses are limited to 4096 bytes and at most 150 seconds (or a
smaller configured timeout), with no automatic retry. Timeout remains uncertain.
The application must persist authorization/intent before calling this method.

## Verification and remaining integration

The four packaging-contract*.json files in backend/tests/fixtures are synthetic
exports from actual service-image HTTP/conversion/restart smoke, not fabricated
success responses. They cover rectangular input, 16-bit maps with multiple
resolutions/current policy, a 3K effective master and an exact-square COPY.
Their report/proof data contains no service token, raw source metadata or absolute
host paths. tests/packaging_service_smoke.py accepts an export path and optional
single, multi-current, nonstandard or square fixture variant.

The client suite initially passed 73 cases; current final results are recorded in
autonomous-pbr-progress.md. Negative tests rehash modified proofs to exercise
semantic validation independently of the outer digest. Transport tests cover
credential/configuration gates, private error handling, bounded reads, redirects,
mutable nested model data and exactly one network attempt on timeout.

The application now supplies immutable execution/attempt history, material-operation
ownership shared with identity/catalog/content gates, account/approval revalidation,
explicit retry/reconcile, downloads and UI; see [actions](packaging-actions.md).
Those ownership gates remain mandatory for any additional client caller. Live
storage/importer verification and application retirement authorization remain
separate. The private retirement transport is implemented; its application and
database integration is tracked in [the retirement plan](packaging-retirement-integration-plan.md).
