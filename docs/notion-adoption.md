# Reviewed local adoption from Notion

ADMIN can compare the company's explicitly linked Notion page, select changed
descriptive fields, enter a reason and confirm the exact local replacements.
Selecting an empty observed optional field clears that local value. Unselected
fields, company identity, Notion link, active flag, projects, brands and permissions
remain outside adoption. No name matching, record creation, automatic sync or
Notion writes occur. Comparison remains available without applying anything.

This conservative ownership rule follows the absence of an agreed automatic sync
policy. The Notion reader uses the explicit deployment mapping documented in
`notion-reader-plan.md`. The same `NOTION_ENABLED` switch controls new comparisons
and adoptions. It is disabled by default; demo/E2E/test deployment clears its token
and mapping. Real mapping compatibility and live Notion behavior are unverified.

## Review and commit

`POST /api/companies/{company_id}/notion-adopt` accepts only:

- `request_key`: a new nonzero UUID for one human decision;
- `expected_page_id`: the explicitly reviewed page UUID;
- `expected_local_sha256`: the comparison's exact local record/time binding;
- `expected_observation_sha256`: the comparison's full mapped observation binding;
- `selected_fields`: one to six unique mapped descriptive fields;
- `reason`: readable nonempty text, at most 2000 characters.

The server normalizes field order, page UUID and outer reason whitespace, binds the
request to its actor/company/operation, and authorizes before checking for exact
replay. New commands must match the current company. The server reads Notion again
using bounded GET requests, then reauthorizes, locks the company and checks both
local and remote comparison bindings. There is no database transaction during
external IO. Unmapped, unchanged, stale or revoked decisions cannot mutate data.

Only server-validated values enter the local company. Company values and one
immutable `NOTION_ADOPTED` history event commit atomically. The event records exact
before/after snapshots, the actor, reason, selected fields, explicit page/source/
database IDs, source edit time and mapping/observation digests. It contains no raw
Notion body or credential. This records the observed values actually adopted;
Notion may change again after the read. It does not claim an atomic remote/local sync.

## Lost responses and recovery

A successful response contains `request_key` and the original immutable `event`.
Repeating the same actor/key/normalized body returns that original event without
remote IO, including after later local edits, link removal, a restart or disabling
the integration. Reusing the key for a different body/company returns 409. Two
backends completing the same request produce one event. A late read failure also
checks for a committed exact replay before reporting failure. Current ADMIN access
is always required, even for historical replay.

`GET /api/companies/{company_id}/notion-adoptions/{request_key}` reads only that
actor's committed result. It sends no request to Notion. A 404 means no matching
committed result was visible at that check; another request can still be running.
Company history lets ADMIN inspect all recorded company changes. All responses
are authenticated and not cacheable.

The UI preserves one exact pending packet for the current actor in memory across
panel collapse and in-app company navigation. It does not resend on opening.
**Check saved adoption result** is read-only; **Retry same adoption** submits the
original key/body. Other company adoptions are blocked until this packet is resolved.
After an unknown outcome, later access denial or absent recovery does not silently
discard it. After full browser reload, inspect durable company history; the packet
is deliberately not stored in browser persistent storage. On verified success the
company profile refreshes. The saved event remains historical, not a promise that
its values are still current.

## Verification and rollback

Contract tests replace only Notion HTTP with synthetic responses. Actual PostgreSQL
tests cover atomic history, current role/session checks, concurrent company/link
changes, duplicate requests across independent backend readers, competing decisions,
no transaction across IO, late successful/failed reads, and replay after restart
with the integration disabled. UI tests cover field review, lost responses, exact
retry, read-only recovery, stale response exclusion and actor/company boundaries.

The browser deployment remains disabled. Its real API rejection and persisted
company/history are tested. Browser comparison/adoption successes, including a lost
response, are explicitly intercepted synthetic UI contracts; they neither call
Notion nor create fake audit success in the deployed database. Do not present these
as enabled-deployment integration or live external verification.

No migration beyond committed 0020 is required. Disable the integration to stop
new adoptions; retain company/history evidence and allow authorized exact replay.
Do not downgrade or delete populated audit history. Correct erroneous local values
through an ordinary audited edit and preserve the original adoption event.
See `autonomous-pbr-progress.md` for exact results and remaining work.
