# Preview gallery and material comparison

The material detail has an **Open preview gallery** action. Select a direct image
from its linked `PREVIEW` directory. **Compare** in the main navigation displays
two materials independently, with a separate image selection for each. Only
materials visible to the signed-in account appear. Reloading the material list
discards any selected material no longer returned by the server.

Supported source formats: JPEG, PNG, TIFF and WebP, one frame only. The server
applies EXIF orientation, preserves proportions and does not upscale. It creates
a JPEG no larger than 1024 pixels per side, stripping source metadata. Alpha is
composited on white. Color profiles and original bit depth are not retained, so
these images support visual review, not calibrated color or numerical map work.
No gallery operation modifies or renames sources or changes their modification
times. Ordinary filesystem access-time behavior remains operating-system owned.

## Access and requests

- `GET /api/materials/{uuid}/previews` returns safe filenames, sizes and SHA-256
  hashes. It does not return source bytes, worker addresses or absolute paths.
- `GET /api/materials/{uuid}/preview?name=...&expected_sha256=...` requires a name
  and exact hash from the listing. A changed source is rejected; reload the
  gallery before selecting it again. The result is `image/jpeg`, `no-store`,
  `nosniff`, with a fixed safe disposition and derived size/hash headers.
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
failed sources. Synthetic E2E verifies real browser image decoding and comparison
on fresh data and after service restart; consult the progress document for the
latest completed run rather than assuming an unrun test has passed.

No migration, source write or new data volume is required. Roll back the gallery
code commits to remove this feature; database revision 0011 remains unchanged.

Live NAS performance, large-library browsing and calibrated preview rendering are
not verified by synthetic tests. Historical import and publication packaging are
separate slices.
