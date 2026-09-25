# Production dashboard

The authenticated home page (`/` or `/dashboard`) now shows the active material
records returned by the existing `/api/materials` endpoint. Its server-side role
and assignment rules remain authoritative: processors see their own assignments,
other roles see the active records they can access, and archived records are absent.
No migration, new API, worker operation or external service is involved.

## Product decisions

- Counts use one loaded material-list snapshot, with explicit manual refresh.
  Loading or failed reads never masquerade as zero counts. A failed refresh
  removes the old records and counts until a successful retry.
- The four views are all materials, IN_PROGRESS, DONE, and recorded validation
  findings (WARNING, ERROR, METADATA_MISSING). NOT_CHECKED is not classified as a
  failure. DONE remains a production status; it is not approval or publication.
  Finding counts overlap the workflow counts and are not a separate workflow stage.
- Search matches name or technical identity within the selected view. Counts
  continue to describe the full loaded snapshot. Results sort by technical identity
  and UUID and display ten records per page. Status/search/refresh reset pagination.
- The page offers ordinary material links and role-appropriate creation/publication
  shortcuts. It never performs a write, polls, checks source files, scans metadata,
  starts packaging or changes approval/publication state.
- Account/role changes remount the page, clearing data, filters and pending results.
  A response from the former account cannot populate the new view. Source paths,
  raw metadata and account credentials are not displayed or saved in browser storage.

## Limits and validation

This is a conservative overview of existing recorded statuses, not an inferred
approval queue or a claim about current NAS bytes. Open the material for its
versioned review, approval and publication details.

Pagination is in the browser: the existing API still returns the full authorized
active list. A representative production volume and server-side list pagination
remain performance acceptance work; no large-dataset performance is claimed.
The overview does not add per-material detail requests or directory scans.

Frontend tests exercise role shortcuts, account/role races, failed refresh/retry,
pagination and search, inert untrusted text, and the distinction between production
completion and publication. The guarded browser suite checks real API counts,
processor scope, read-only refresh/error recovery, navigation and 390px layout
on both fresh and retained data. Exact run results are recorded in the
[progress checkpoint](autonomous-pbr-progress.md).

Rollback is a frontend release reverting this page, its routing and styles. There
is no database or filesystem rollback and no change to the external acceptance
requirements in the [review handoff](pbr-review-candidate.md).
