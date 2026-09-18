# Next slice: explicit renewable GCS credentials

The existing injected async provider is useful for transport tests, but deployment
currently accepts only a static access token. Keep that default compatible and add
an explicit `adc` mode; never discover an ambient account merely because a static
token is missing. Disabled GCS must not import/discover credentials, open files or
make a request. Reject ambiguous configuration containing both modes.

Use Google's authentication library to discover and refresh credentials only after
the durable operation guard authorizes the call. ADC can consult configured files,
local SDK credentials or the runtime metadata service; its deployment environment
must therefore be intentionally configured. Credential configuration is operator
input, never a material field or browser upload. Google's library documents both
the [discovery order and the need to validate externally supplied configuration](https://google-auth.readthedocs.io/en/latest/reference/google.auth.html).

Keep credential state in memory, serialize refresh and refresh before expiry with
a safety margin. Perform blocking SDK work outside the event loop, enforce bounded
credential transport and sanitize all failures. A cancelled credential lookup must
not release its own concurrency guard while its worker is still running. Recheck
application authority after obtaining a credential and before each storage request.
Long uploads need a fresh token between HTTP requests, without replaying a failed
storage write. The official [credential API](https://google-auth.readthedocs.io/en/latest/reference/google.auth.credentials.html)
provides explicit refresh; do not use an authorized session that silently retries
the application's upload after a 401.

Synthetic tests must cover disabled/static isolation, expiry and refresh failure,
concurrent/cancelled refresh, role revocation during refresh, request-level renewal,
no automatic storage retry, connection cleanup and absence of sensitive diagnostics.
Run against the real installed auth library with injected local transports; no
ambient credentials or real Google endpoint may be used during these tests.

This document is a plan, not completed ADC support or live verification. Deployment
credential selection, actual IAM grants and an isolated live target remain explicit
operator responsibilities; this development run will not change external accounts.
