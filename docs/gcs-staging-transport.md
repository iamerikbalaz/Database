# GCS staging transport (not a complete publication feature)

`backend/app/gcs_client.py` implements the real HTTPS JSON API transport. It is
disabled by default and is not exposed by an application route yet. Tests use an
in-memory HTTP transport with synthetic data; no live bucket or credential was used.
There is no fake production success path.

## Contract and configuration

`GcsClient.from_settings(settings)` uses `GCS_ENABLED`, `GCS_BUCKET_NAME`,
`GCS_STAGING_PREFIX`, `GCS_ACCESS_TOKEN` (secret) and `GCS_TIMEOUT_SECONDS` (default
900, maximum 3600). Enabling requires an explicit bucket, prefix and short-lived
OAuth access token. Inject the token through the service's secret environment,
never command-line arguments, tracked files or logs. Token refresh is not provided
by this initial provider; expiry fails closed. A server-owned async token provider
can instead be passed to `GcsClient`. No ambient account, credential file or gcloud
subprocess is consulted. `GOOGLE_APPLICATION_CREDENTIALS` is not consumed yet.

Use a dedicated staging target with create/get permissions and an appropriate
bucket access policy. The adapter neither changes ACLs nor requests public access.
`storage.objects.create` supports new uploads; overwrite permission is unnecessary
for this adapter's create-only behavior. [Google insert reference](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/insert).
Verification needs `storage.objects.get`; it does not need object listing or ACL
reads. [Google get reference](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/get).
No account permissions were changed in this task.

Each `GcsObjectSpec` binds an upload-job UUIDv4, immutable input-binding SHA-256,
relative path, positive size up to 16 GiB and expected SHA-256. The destination is
`<configured-prefix>/<job-uuid>/<relative-path>`. Empty objects are unsupported in
this first version. Paths have strict length, depth and traversal checks. This is
an internal staging convention, **not a claimed match for the deployed importer**.
The application coordinator must derive these inputs from immutable approved jobs;
arbitrary client input must never serve as authorization to upload.

## Transfer and verification

The initial resumable request carries `ifGenerationMatch=0`; an existing live object
causes a conflict. No overwrite, delete, ACL update, copy or promotion operation is
implemented. Generation and metageneration conditions protect subsequent reads.
[Google precondition reference](https://docs.cloud.google.com/storage/docs/request-preconditions).

The source supplies bounded 64 KiB blocks. Uploads use 8 MiB chunks, with an exact
acknowledged offset required after each nonfinal write. The last chunk waits for
source EOF, exact length and the expected SHA-256. A failure in the source's final
validation therefore cannot finalize an upload. The session URL is accepted only
for the configured bucket on the fixed HTTPS Google endpoint; redirects and ambient
proxies are disabled. The URL is a secret capability and exists only in memory.
There is no persisted session or automatic write retry.
[Google resumable protocol](https://docs.cloud.google.com/storage/docs/performing-resumable-uploads).

Completion requires exact returned object identity/provenance, full generation-bound
readback with an independent SHA-256 and size check, and a final conditional lookup
of the live object. Custom metadata alone is not byte verification. No reliance on
optional MD5 fields is required. Metadata reads are capped at 32 KiB; no full ZIP is
buffered. One operation per adapter instance, bounded IO, a whole-operation timeout,
shielded connection cleanup and fixed error codes limit failure behavior.

HTTPX/HTTPcore diagnostics are suppressed only inside this operation's async context,
including client cleanup, because HTTP request URLs and response headers can expose
the session capability. Other operations retain their normal logging. Do not add
request/response hooks, raw-body logs or credential-provider diagnostics that expose
secrets. The adapter never returns session URLs, tokens or remote error payloads.

## Failure and recovery

Any failed or cancelled upload may have remote side effects. Persist that uncertainty
in the future coordinator; **never treat an exception as proof of absence**. A lost
completion response is recovered with `reconcile(spec)`: it only reads the exact
object, verifies its provenance, generation and all bytes, and checks that it is
still current. It cannot continue a partial resumable session. A 404 is a read-time
observation, not proof that a previously dispatched operation can never complete.

Use a new explicitly owned attempt/job namespace for any later replacement attempt;
keep old uncertain namespaces isolated. This adapter does not erase partial data or
local retained packages. Garbage collection and the product's derived-file cleanup
rule need a separate lifecycle design that preserves immutable history and excludes
source/master/PREVIEW files. A verified receipt is a point-in-time storage observation;
later remote deletion or changes remain possible.

## Remaining integration work

- Durable batch/attempt ownership, current approval/session checks before and after
  external IO, immutable receipts and concurrency fencing.
- Exact CSV plus artifact mapping, whole-batch completion, importer-layout validation
  and a completion marker that incomplete namespaces cannot impersonate.
- Real credential lifecycle, live isolated-target verification when separately
  authorized, and throughput/cost assessment for full readback.
- Explicit manual CSV-import confirmation with online IDs/date/hash. A single object
  receipt cannot set UPLOADED_WAITING_FOR_CSV_IMPORT or PUBLISHED.

Run offline contracts with `python -m pytest tests/test_gcs_client.py tests/test_config.py`.
The cases cover multi-chunk and exact-boundary transfers, final-source failures,
lost completion and read-only recovery, corrupt/truncated/changed remote objects,
conditional conflicts, secret-bearing response URLs, malformed proofs, token
validation, bounded errors, timeouts, cancellation cleanup and logging isolation.
