# Compact cards, full-quality previews and storage paths

## Implemented behavior

- Material folder and library panels stretch to the same row height on desktop
  and stack on narrow screens. Library metadata uses compact label/control rows:
  Description, Color HEX, Tags and Sample size (W x H), with both dimensions on
  one line. Properties group selects, checkboxes and text values; Note and the
  history heading match the other card controls.
- Clicking the material card image or an expanded Materials preview opens a
  modal full-quality viewer. It starts on the clicked image and supports previous
  and next images, arrow keys, Escape, fit-to-window and 1:1 pixels. Originals
  load only when requested; closing or switching aborts unfinished requests and
  releases image object URLs. Thumbnail loading is unchanged.
- Orders and Customers cards and lists use compact controls and responsive rows.
  Order folder references show an ellipsized path and small copy icon. Copying
  retains the complete path; hovering also reveals it.
- Keep filters sits in the heading after Add across the database pages.
  Horizontal wheel events, including MX-style deltaX and Shift+wheel, now drive
  the table and top scrollbar together. Ordinary vertical scrolling remains native.
- Settings / Paths offers a native Windows folder chooser next to all four path
  inputs, as well as manual entry. Picking fills the field; Save paths applies it.
  Picker cancellation does not change the field. Remote clients use manual entry.

## Original-image contract

The new authenticated `GET /api/materials/{id}/preview-original` uses the listed
source hash and rechecks material and folder access after IO. PNG, JPEG and WebP
are returned byte-for-byte after format and full decode checks. TIFF is encoded
as a full-resolution lossless PNG, retaining 16-bit values and ICC profiles.
Native Windows holds source and ancestor read handles throughout decoding;
the Linux worker checks descriptor/source identity before returning. Existing
image byte, pixel, format and concurrency limits apply. Nothing writes to PREVIEW.

The worker exposes the matching internal endpoint and should be deployed with
the backend. The local test instance uses the native Windows adapter.

## Published library decision

The fourth path defaults to `Z:\3. LIBRARY\3.3 PBR MATERIALS LIBRARY`.
It is a stored destination for future publication snapshots, arranged by
customer/material like processed materials, excluding SOURCE. **This iteration
does not implement or run snapshot copying.** The configured Z: folder was not
accessible on this host during verification.

This destination can be saved while disconnected, with absolute-path and overlap
checks. It grants no filesystem capability. A future copy operation must validate
the connected root and its authorization again before writing. The other three
roots still must exist; live material operations remain restricted to Test_data.
Existing immutable settings revisions receive the default when read, without
rewriting old snapshots. No schema migration is needed (schema remains 0042).

## Verification

- Full frontend: 101 files / 1,259 tests passed; lint and production build passed.
  Vite retains the existing large-main-chunk advisory.
- Preview backend/native Windows suites: 99 passed. Worker preview suites ran in
  an isolated Linux Docker container: 74 passed, no skips. Regressions cover
  source races, revoked folder access, stale hashes, TIFF bit depth and ICC data.
- Paths backend/native-picker suites: 59 passed, 1 skipped (Windows symlink
  creation privileges). Includes old snapshots/replays, offline library paths,
  admin/CSRF/loopback restrictions, cancellation and authorization after dialog IO.
- Paths UI/client suites: 14 passed. Viewer, compact cards, clipboard, wheel
  direction and filter placement are included in frontend tests.
- Local application restarted successfully at `http://127.0.0.1:53033`.
  Authenticated acceptance read 51 materials, verified original PNG bytes against
  a Test_data source at 1200 x 1200, checked its unchanged hash/mtime, checked the
  256px thumbnail, Paths defaults/capability, Orders/Customers and built assets.
  No material/source mutation, copying or path-setting update was performed.
- Fresh browser visual verification and physical MX Master testing were not
  performed. The OS picker helper was tested without opening a live user dialog.

## Runtime and rollback

Work remains on `codex/customer-scroll-pilot`, without merging main. The previous
iteration is `38b3fdb`. To roll back, restore that version, rebuild its frontend
and restart the owned local server; no database rollback is required. Production
and source NAS libraries were not changed. Local runtime configuration and test
evidence are in ignored `tmp/local-materials-v5-20260928` and
`tmp/iteration-20261001`; credentials must not be copied into documentation.
