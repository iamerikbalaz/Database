# GCS credential renewal

`GCS_AUTH_MODE=access_token` remains the default. It uses the explicitly supplied
`GCS_ACCESS_TOKEN`; missing/expired credentials never trigger ambient discovery.
The transport asks its provider before each storage HTTP request and checks current
application authority before and after that call. It never retries a storage write
automatically after a 401 or a refresh failure.

## Opt-in ADC

Set `GCS_AUTH_MODE=adc` and leave `GCS_ACCESS_TOKEN` empty to use the server's
Application Default Credentials through `google-auth`. Both modes still require
`GCS_ENABLED=true`, the explicit bucket/prefix and normal authorized staging jobs.
Disabled GCS performs no discovery or credential IO. The client constructor does
not discover credentials; the first authorized operation does.

Configure this service's runtime deliberately. ADC can use
`GOOGLE_APPLICATION_CREDENTIALS`, an SDK ADC file or a Google runtime metadata
service, according to [Google's discovery order](https://google-auth.readthedocs.io/en/latest/reference/google.auth.html).
Credential configuration is trusted operator input, never an uploaded material
field. Validate externally supplied configuration before installation, as required
by that library. Mount required files read-only and outside the repository and NAS
material roots; never copy a developer's home directory into a deployment image.
No credentials were read from the host or provisioned in this development run.

The provider requests the Cloud Storage read/write scope. IAM must separately limit
the service to its dedicated staging target and required create/get operations.
This code does not grant permissions, change ACLs or authorize publication.

## Refresh and failure behavior

- Credentials and tokens stay in memory. A valid cached token is reused; credentials
  refresh when fewer than two minutes remain. Missing expiry, malformed tokens,
  foreign-universe credentials and unsuccessful refresh fail closed.
- Blocking SDK work runs outside the event loop. A provider lock prevents concurrent
  refresh. Caller timeout is 30 seconds; a cancelled SDK worker may finish cleanup
  afterward and retains its lock until then. A later caller cannot use it concurrently.
  A result finishing after the deadline is discarded.
- Credential HTTP uses the supplied bounded transport: HTTPS, no proxy discovery or
  redirects, at most 12 requests, 64 KiB responses and short individual timeouts.
  Plain HTTP is allowed only for the standard Google metadata hosts and paths.
  Compute credentials are pinned to public `googleapis.com` through the SDK copy
  API, preventing its lazy universe property from creating a separate transport.
- Credential errors expose only `GCS_CREDENTIAL_UNAVAILABLE`. SDK/transport logging
  is suppressed in that credential operation's context; unrelated requests retain
  normal logging. Deployment log handlers added dynamically must retain these
  filters. HTTP request/response dumps and process-memory dumps are not safe logs.
- An operation that loses application authorization during renewal stops before its
  next storage request. Previous uploads can still exist: use explicit read-only
  reconciliation and the durable staging history, not an assumed rollback.

Google's [credential refresh API](https://google-auth.readthedocs.io/en/latest/reference/google.auth.credentials.html)
is used directly. The application does not use `AuthorizedSession`, whose request
retry behavior would conflict with explicit storage recovery.

## Supported verification and remaining limits

Offline tests use the installed Google SDK with synthetic OAuth file credentials,
generated ephemeral service-account signing keys and Google metadata responses.
They exercise real refresh parsing, cache/expiry,
concurrency/cancellation, bounded HTTP, errors/logging, disabled-mode isolation,
request-level renewal and authorization loss. Other SDK ADC sources can require
additional platform/network behavior; AWS metadata, custom plain-HTTP identity
endpoints, executable credential sources and sovereign Google universes are not
verified deployment targets for this adapter. Do not assume broad SDK support is
the same as tested application support.

No live Google account/bucket was used. Live IAM, workload identity configuration,
key rotation, long-running production uploads and actual importer compatibility
still need separately authorized isolated verification. Replacing a credential file
does not immediately evict a cached credential: restart the service in a controlled
window to select a different source identity. Never switch identity to recover an
unknown storage outcome without reviewing that job's preserved evidence.

Local final transport/ADC suite: **136 passed, 3.80s**. Linux regression with
network disabled: **280 passed, 275.89s**, no skips, two dependency warnings,
including archive, staging runtime, reservations and existing GCS transport tests.
The two final service-account signing/error cases were added after that Linux
snapshot and passed locally with the same Google Auth 2.58.0 SDK. No migration or
frontend contract changes are required for this feature.
