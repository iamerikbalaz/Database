# Material list and preview grid

The **Materials** page switches between **List** and **Gallery** under the same
filters. Gallery shows a regular grid of square previews, with a small material
name and technical identity underneath. **Small**, **Medium**, **Large** and
**Extra large** adjust tile size; the grid fits the available width. Only the
display mode and size preference are saved in browser storage.

Each tile uses direct PNGs from the linked material's `PREVIEW` directory.
`FABRIC_1.png` takes priority, then `SPHERE_1.png`, case-insensitively; when neither
exists, the first other PNG in natural filename order is shown. Small corner
arrows cycle all PNGs and show the current position. Clicking the preview or its
caption opens the detail. Missing folders/images remain explicit empty states;
failed reads have a **Retry preview** action. The former two-material Compare
screen and navigation item are removed; old `/compare` links open this grid.

Only materials returned by the current authorized/filter-scoped list are shown.
Switching List/Gallery retains filters without requesting the list again. The
detail still has **Open preview gallery**, including the other formats below.

Supported source formats: JPEG, PNG, TIFF and WebP, one frame only. The server
applies EXIF orientation, preserves proportions and does not upscale. It creates
a JPEG at the requested maximum of 256, 512 or 1024 pixels per side, stripping source metadata. Alpha is
composited on white. Color profiles and original bit depth are not retained, so
these images support visual review, not calibrated color or numerical map work.
No gallery operation modifies or renames sources or changes their modification
times. Ordinary filesystem access-time behavior remains operating-system owned.

## Access and requests

- `GET /api/materials/{uuid}/previews` returns safe filenames, sizes and SHA-256
  hashes. It does not return source bytes, worker addresses or absolute paths.
- `GET /api/materials/{uuid}/preview?name=...&expected_sha256=...&size=256` requires a name
  and exact hash from the listing. A changed source is rejected; reload the
  gallery before selecting it again. The result is `image/jpeg`, `no-store`,
  `nosniff`, with a fixed safe disposition and derived size/hash headers. `size`
  accepts only 256, 512 or 1024; omission retains the 1024-pixel detail contract.
- Backend session, role, processor assignment and active identity-operation
  checks run before and after worker IO. A processor cannot browse a linked
  folder whose basename differs from the material identity. Other authorized
  roles can inspect previously linked mismatches for review.
- Worker endpoints `/internal/material-previews` and `/internal/material-preview`
  are internal dependency routes. Keep the worker on the private application
  network; do not publish it as a public image service.

The browser fetches authenticated application bytes and creates a temporary object
URL. Selection changes, retries, closing, navigation and session unmount abort
pending reads and revoke image URLs. No image URL or auth token is persisted.
Details start closed to avoid scanning or decoding images until requested.

## Grid loading and freshness

Tiles request data only within 250 pixels of the viewport; offscreen tiles cancel
pending work and release their decoded image URL. Small tiles request 256-pixel
JPEGs; other grid sizes request 512. A shared queue limits the page to two active
preview requests, including listings. It removes canceled queued work before HTTP.

An in-memory, page/session-owned cache holds at most 200 listings and 160 image
blobs within a 32 MiB budget. Entries are reusable for 60 seconds. Image keys bind
material, folder, filename, source hash and requested size. Authentication changes,
navigation away and **Refresh previews** discard the cache; late responses cannot
repopulate a discarded cache. Each live tile owns and revokes its object URL.
No images, filenames, folder paths or authentication data are persisted.

An already displayed image is a snapshot, not a live filesystem watcher. Use
**Refresh previews** after changing sources. Cache hits do not contact the server;
new reads retain server reauthorization. The first uncached listing still hashes
eligible sources, and the worker still rechecks/decode-renders each uncached
image. These controls reduce browser transfer and repeat work; they do not prove
live NAS throughput or provide a persistent server thumbnail cache.

## Bounds and failures

| Resource | Limit |
| --- | --- |
| Direct PREVIEW directory entries | 512 |
| Eligible images | 64 |
| Individual source | 64 MiB |
| Combined eligible sources in listing | 512 MiB |
| Listing deadline | 15 seconds, checked between IO operations |
| Source decoded pixels | 32 × 1024²; longest side at most 32768 |
| Derived JPEG | 2 MiB; at most 1024 × 1024 |
| Worker response | 3 MiB |
| Simultaneous listing/decoder work | 2 per worker process; no queue |
| Decoder child | 768 MiB address space, 20 CPU seconds, 25-second parent timeout |

Linux directory descriptors and no-follow opens bind all reads to the configured
materials root. Symlinks, hardlinks, special files, unsafe names and cross-device
entries are rejected. Other ordinary files and regular nested directories are
omitted with a count; nested files are never traversed. Listing and rendering
check source signatures and path bindings before/after reads. Every eligible
listing entry is checked again after the final file was hashed.

Busy or unavailable workers produce honest errors; corrupt/unsupported images
produce no substitute success image. Internal diagnostics, EXIF, raw metadata and
absolute paths are not forwarded to the browser. The source remains authoritative;
there is no durable derived-image cache in this slice.

## Verification and rollback

Linux tests use actual encoded images, metadata and filesystem races. Backend
tests cover actual sessions/assignment, reauthorization after worker IO, response
bounds and malformed contracts. PostgreSQL concurrency tests pause the worker and
perform real assignment, account-disable and identity changes through the API.
UI tests cover selection, late responses, cancellation, URL revocation, empty and
failed sources. Synthetic E2E verifies real browser image decoding, grid sizes,
primary filename priority, arrows and mobile layout
on fresh data and after service restart; consult the progress document for the
latest completed run rather than assuming an unrun test has passed.

No migration, source write or new data volume is required. Roll back the gallery
code commits to remove this feature; this grid increment has no migration and
does not change existing revisions through 0026.

Live NAS performance, large-library browsing and calibrated preview rendering are
not verified by synthetic tests. The separate [100-material R: acceptance](historical-r100-acceptance.md)
uses a dated local preview snapshot because its Docker worker cannot mount R:.
That private adapter is not part of the production worker or repository code.
