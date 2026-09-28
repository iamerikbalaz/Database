# Automatic file check

The materials table displays a server-produced field independent of human
**Checked** and the older technical validation/approval records. Users cannot
PATCH this field. The material card and the explicitly selected bulk operation
run the same inspection and append the actor to material activity.

## Current preliminary profile

`BASIC_V1` inspects safe folder access, the file tree, `metadata.json` readability
and scalar completeness, and supported preview availability. It does not enforce
the final map, resolution or packaging rules: those will be agreed in the next
iteration. It does not alter any material source files.

- Actual observations of missing or incomplete data result in **issues**.
- A clean preliminary inspection remains **not checked**; its report and timestamp
  explain that the preliminary inspection completed and final rules are absent.
- **OK** is reserved for a future complete validation profile. A client or desktop
  adapter cannot promote `BASIC_V1` to OK.

The database stores the status, last inspection time, report, profile version and
whether the configured validation completed. Changes that invalidate material
source review also clear this derived result. The immutable activity record keeps
the earlier observation. Manual changes made outside the app are detected on the
next inspection; the timestamp describes the last observation, not live monitoring.
The full report remains in the database and audit evidence and is returned by the
explicit check operation and bulk TXT export. Material list/detail DTOs expose only
the compact status, date, profile and completeness fields; ordinary edit receipts
do not copy potentially large reports or local paths.

## Bulk reports

`POST /api/materials/check-data` accepts 1–100 distinct materials, each with its
current `expected_updated_at`. It checks authorization for the entire selection
before reading sources and rechecks authorization, versions and operation locks
before persisting any result. Editors can inspect their permitted materials;
read-only Leadership accounts cannot write check results. The desktop endpoint is
loopback-only and requires the normal authenticated CSRF protection.

The TXT report is saved under `%LOCALAPPDATA%\REAWOTE\Reports\Checks` with a
timestamp and unique ID. This durable location is outside `Test_data` and does
not disappear during temporary-file cleanup. An explicit bulk check opens its
report in Notepad, including an optional check during publication review.
When launched through a packaged Windows app, Windows can redirect this folder
under `%LOCALAPPDATA%\Packages\<package>\LocalCache\Local\REAWOTE`. The report
adapter resolves and validates that fixed physical location and displays the
actual saved path. Material-source path checks are unchanged.
`open_report: false` supports integrations and automated tests without opening
an editor. The response includes
the report text even if the desktop editor or report storage is unavailable, so
the UI can offer a TXT download. Reports contain local paths and remain local.

Migration `20260928_0033` adds the five fields without reclassifying historical
validation states. Existing materials begin with **not checked**.
