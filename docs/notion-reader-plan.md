# Notion: explicit company-source reader

## Bounded first slice

The only existing Notion identity in this application is `Company.notion_page_id`.
No actual workspace schema, property IDs, project/company relationship or conflict
ownership policy has been supplied. The first adapter therefore reads one explicitly
linked company page in one configured data source, validates its schema and returns
only explicitly mapped descriptive fields. It does not search for a matching page,
create records, apply changes, set active flags or write to Notion.

Configuration is disabled by default. An enabled reader needs an explicit token,
data-source UUID and stable property-ID mapping for `name` (title), with optional
`legal_name`, `country`, `address`, `vat_id` (rich text) and `website` (URL). Display
names are not used to infer a match. User roles, project ownership, materials and
publication state are outside this mapping. Project/company relations must never
be guessed from names.

Pin `Notion-Version: 2026-03-11`. Only GET requests to the fixed HTTPS Notion origin
are allowed, with fresh clients, no redirects, proxy inheritance or persisted
cookies. Revalidate configuration and page identity before IO; validate the schema,
page parent, nontrashed state and exact property IDs/types before returning values.
Limit response bytes, property counts, rich-text fragments and total duration.
Mention/formula/relation values are unsupported; never silently flatten or truncate
them into a purportedly complete company field. Do not follow URLs in properties.

The output is a point-in-time observation with a digest of its explicit binding and
mapped values. It is not a synchronized database record or a transactional remote
snapshot. A future review/apply API must recheck current local authorization, the
local source link and both versions, persist a durable audit and perform explicit
conflict handling. A future remote writer needs a separate durable dispatch/recovery
contract; this reader must not grow automatic bidirectional updates.

## Test and rollout plan

1. Pure configuration/schema/page projection tests and HTTPX synthetic transport
   tests: exact target/version, malformed/partial data, redirects, bounded streams,
   timeout/cancellation, access errors, rate limits and secret-safe diagnostics.
2. Administrator-only comparison API with before/after authorization and no database
   transaction over network IO; local application/concurrency tests.
3. Explicit operator comparison UI and safe local adoption after audit/idempotency
   support; isolated E2E. Actual linked company schema is a separate integration gate.
4. Consider additional resources and synchronization directions only with documented
   ownership/mapping and conflict rules. No production/external writes in this task.

No migration is needed for the transport slice. Removing an unused reader module
rolls it back without changing stored data. Nothing reads a real account during
development or at application startup.

## Application comparison and configuration

The implemented routes are `GET /api/integrations/notion` and
`POST /api/companies/{company_id}/notion-preview`, restricted to current ADMIN
accounts. POST requires the normal session CSRF token and a JSON body containing
only `expected_page_id`, the exact company's linked page UUID (canonical or compact).
The status route discloses enabled state and mapped field names, without credentials
or property IDs. Preview validates the local link before sending either GET to Notion,
checks current access and all descriptive company fields again at IO boundaries and
before responding, and returns current/observed values with a changed flag. A changed
local record returns 409; revoked access returns 401/403 even after a remote failure.
No database transaction or account lock remains open while waiting for Notion.
All responses use `Cache-Control: no-store`. These reads create no synchronization
history and apply no values. The observation hash is evidence for a comparison,
not an authorization token or a durable saved snapshot.

Environment settings (also passed to the backend by the normal Compose file):

| Setting | Meaning |
| --- | --- |
| `NOTION_ENABLED` | `false` by default; explicitly opt in |
| `NOTION_ACCESS_TOKEN` | Separate integration credential, supplied through secure environment injection |
| `NOTION_COMPANY_DATA_SOURCE_ID` | Canonical nonzero data-source UUID |
| `NOTION_COMPANY_PROPERTIES` | JSON mapping of supported local field names to exact stable Notion property IDs |
| `NOTION_TIMEOUT_SECONDS` | Whole remote read deadline, default 20, maximum 60 seconds |

For a source whose title property ID is `title`, the smallest mapping is
`{"name":"title"}`. Add other fields only after checking their real IDs/types and
ownership policy. Missing optional mappings leave those fields out of the comparison;
a mapped empty field remains an explicit null. The old placeholder settings
`NOTION_API_TOKEN`, `NOTION_COMPANIES_DATABASE_ID`, and `NOTION_PROJECTS_DATABASE_ID`
are not consumed. There is no inferred database-to-source lookup. The demo, E2E and
isolated test runner explicitly disable the integration and clear its configuration.

One reader operation is allowed per backend process. A 429/529 installs a bounded
process-local cooldown and returns `Retry-After`; no automatic retry, sleep, page
search, follow-up crawl or background synchronization occurs. This is not distributed
rate coordination across multiple backend instances. HTTP diagnostics suppress
private URLs/headers/bodies in the reader's IO context; failures use fixed codes.
Disable `NOTION_ENABLED` and restart the backend to turn the feature off; no data or
schema rollback is needed. Live read verification needs an explicitly shared test
source and approved field mapping, neither of which has been supplied.

## Company detail controls

ADMIN can open **Notion company comparison** in a company detail. Opening reads only
the application configuration. **Compare with Notion** explicitly requests a fresh
remote observation, available only when enabled and the company's saved page ID is
valid. The panel shows the server-read local value beside each observed value,
including an explicit empty value and a Same/Different indicator. It displays the
remote last-edited time and offers expandable source/digest details. Property values
are rendered as text, including websites; the panel does not follow property links.

Each new read clears the previous result. Closing the panel, changing the company,
local record timestamp or signed-in actor discards pending UI results. Failures show
fixed guidance and never trigger automatic retries. No apply/save control exists in
this slice; durable audit and conflict-checked adoption remain separate work.
Browser verification checks the real disabled API and retained company link, then
an explicitly intercepted synthetic comparison UI. That second phase is a frontend
contract test, not live Notion or enabled-deployment integration verification.

## Primary contract references (checked 2026-09-18)

- [Retrieve a data source](https://developers.notion.com/reference/retrieve-a-data-source):
  schema retrieval uses a data-source ID, distinct from its database container.
- [Parent objects](https://developers.notion.com/reference/parent-object): current
  data-source page parents include the source ID and containing database ID.
- [Retrieve a page](https://developers.notion.com/reference/retrieve-a-page) and
  [page properties](https://developers.notion.com/reference/page-property-values):
  properties are separate from page body; references can be truncated by page reads.
- [2026-03-11 upgrade](https://developers.notion.com/guides/get-started/upgrade-guide-2026-03-11):
  use the pinned version and `in_trash` field.
- [Request limits](https://developers.notion.com/reference/request-limits): honor
  `Retry-After` for 429/529; no fixed assumed account request budget.

Production mapping, live verification and synchronization remain unfinished.
