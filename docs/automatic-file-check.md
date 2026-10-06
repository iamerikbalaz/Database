# Automatic file check

The materials table displays a server-produced field independent of human
**Checked** and the older technical validation/approval records. Users cannot
PATCH this field. The material card and the explicitly selected bulk operation
run the same inspection and append the actor to material activity.
The **Automatic check** filter selects not checked, OK or issues and combines
with the other Materials filters in the list, gallery and archive.

## Stored reports (2026-10-06)

Click the **issues** or **OK** status in Materials, the material card, or the
publication preparation table to open the last saved report and download its
TXT version. The dialog shows its recorded time and inspection profile. This
read does not run another check or require a connected desktop file adapter.

Reports from single and bulk checks remain in the database after an application
restart. If a material change invalidates its automatic status, the card's
**Last report** action opens the retained evidence with a **Historical report**
notice. It does not certify the changed files. Editing files outside the app
still requires a fresh check; merely opening a saved report does not detect it.

`GET /api/materials/{id}/automatic-file-check-report` follows ordinary material
visibility, requires authentication, and permits archived reads only for ADMIN.
Deleted records are unavailable. Responses are not cached; the UI displays
bounded plain text and removes absolute desktop/NAS locations. Existing durable
material fields and audit events supply the evidence; no schema migration is
needed.

## Full validation profile

`PBR_FILES_V1` is the complete automatic file check used by the material card and
the selected-material bulk operation. It reads source files without changing them.
The offline publication exporter applies the same file rules before packaging.
This is a technical inspection, separate from the human **Checked** review.

- **OK** means that the full inspection completed and found no issues.
- **issues** means that the inspection found defects. The report identifies the
  affected file or folder, the observed problem and the expected value.
- **not checked** means that no current complete clean result exists. Relevant
  changes made through the app invalidate the previous derived result; rerun the
  check after editing files outside the app.
- Infrastructure failures or a source changing during inspection are not treated
  as a successful check. Existing results are not replaced with a false **OK**.

Earlier `BASIC_V1` results retain their preliminary meaning. That profile only
inspected folder access, metadata and preview availability. It cannot certify a
material as **OK**, even if an adapter reports no issues. Rerun the full check to
replace a historical preliminary result.

### Desktop configuration

The trusted desktop launcher installs `LocalMaterialFileCheck` on its
`LocalMaterialLibrary.file_checker`. The adapter requires an absolute Docker
executable, the local `desktop-linux` context, an immutable packaging image ID
containing `app.local_file_check`, and a private job directory outside both the
material library and its mutation journal. A full selection uses one container.
The container has no network, a read-only source mount and bounded resources;
Windows source handles block writes and renames while the check is running.
Large map images in the full profile may use up to 120 seconds of wall time per
decode, clipped by the remaining material deadline, to allow slow desktop file
I/O. The decoder still has a 30-second CPU limit and 2 GiB memory limit. The
legacy technical decoder retains its original 35-second wall limit.
An unconfigured desktop keeps the explicitly preliminary BASIC profile.
The ordinary remote worker approval endpoints retain their earlier versioned
contract; their historical approval evidence is not reclassified as a full check.

### Desktop performance and progress

The automatic desktop check processes at most **two materials concurrently** in
separate worker processes, within one container limited to 2 CPU and 6 GiB RAM.
While hashing each source input, it copies the same bytes into a private temporary
directory on a Linux Docker volume. Decoding reads that copy instead of repeatedly
crossing the Windows/Docker filesystem boundary. Staging is limited to 8 GiB per
material and is removed after the material completes; container cleanup also
removes its anonymous volume. Exceeding a resource limit fails the check, never
certifies a material as OK.

Successful full image decoding can be reused from an authenticated evidence
cache. Each entry is bound to the file's SHA-256 content, immutable worker image,
and evidence version. It stores dimensions, format, depth and color mode, not
image bytes or a material approval. Current names, required maps, metadata and
cross-image rules are checked again. Modified image content gets a new decode.
Malformed, unsigned, incompatible or missing entries are cache misses; decoding
failures are not cached. The cache is bounded; saturation falls back to decoding.
The signing key lives separately under a private Windows ACL and is mounted
read-only. It never appears in the HTTP response or report.

The original source is still hashed again at the end of every material, including
cache hits. Native Windows source handles and the final whole-tree comparison
remain in place. This optimization does not change publication's full inventory,
approval proof or packaging policy.

The card, bulk selection and optional publication check use
`POST /api/materials/check-jobs` followed by `GET /api/materials/check-jobs/{id}`.
The POST uses the existing selection/version body plus a UUID `Idempotency-Key`.
Progress includes completed/total materials, up to two active materials and their
relative filenames, elapsed time, and image cache hits/misses. Completion is only
reported after the existing atomic database save and report step.

Every start, retry and poll requires the authenticated local desktop connection;
the entire selection is reauthorized, and job results are owner-only. A lost POST
reply reuses the same key; a lost polling connection offers **Resume same check**
and continues observing the same job. Neither automatically launches a new scan.
Leaving the page stops observation, not the server-side check. Authentication or
missing-job errors stop observation and require an explicit new user action.

Jobs are process-local: restarting the app loses them. Up to eight terminal result
records are kept for at most one hour; older records can be evicted earlier when
new jobs arrive. Compact used-key records survive result eviction for 24 hours
(up to 1,024 keys); retrying such an evicted job returns not-found rather than
silently repeating work. A full used-key table rejects new jobs until records
expire. The legacy synchronous check endpoints remain available for integrations.

Measured on the 50 local Test_data materials on 2026-09-29, with other test jobs
stopped: the first run with an empty evidence cache took **293.6 seconds**, and
the immediate repeat took **123.2 seconds**, versus the preceding approximately
18-minute run. Both produced exactly the same issue lists and **23 OK / 27 issues**.
The repeat reused all 615 image inspections with zero decoder misses; source
files and other material properties stayed unchanged. Actual performance on other
disks, NAS connections and material sets will depend on source sizes and I/O.

### Inspection scope

The automatic check reads and hashes the files relevant to its rules: source
resolution directories, `PREVIEW` and root metadata. It decodes map and preview
images and parses metadata. It does not decode or hash unrelated payloads in
folders such as `SOURCE`; these files are outside the automatic validation rules.
The native desktop adapter still holds existing files throughout the selected
material trees stable and compares the tree before and after the inspection.

Publication retains the full source inventory and content hashes used to bind
packaging to unchanged input data. The narrower automatic-check inventory does
not replace that publication evidence. An **OK** automatic result certifies the
configured map, preview and metadata rules at the recorded inspection time, not
the contents of unrelated source payloads.

### Folder and map rules

The material folder uses uppercase
`BRAND_0001_MULTI-WORD-MATERIAL_CATEGORY`, retaining every word of the material
name and separating words with hyphens. The number contains exactly four digits.
Map filenames identify that same material and include the exact map token and
resolution, for example `BRAND_0001_MULTI-WORD-MATERIAL_COL_4K.jpg`.
The existing compatible form including the category before the map token remains
accepted. A map from another material or an unknown map token is an issue.

There is one source resolution folder: the highest/master resolution, for example
`4K`. Lower resolutions are generated for publication. That folder must contain
**COL**, **ROUGH** and **NRM**. **GLOSS** is optional and does not replace ROUGH;
**NRM16** does not replace NRM. Additional maps are optional but, when present,
must meet their respective rules. Duplicate map types are issues.

All maps must match the COL width and height. The folder's K number is the number
of complete thousands in the longest side: `8K` accepts **8000–8999 pixels**,
including 8192 and 8600. `4K` accepts 4000–4999 pixels. Rectangular images are
allowed. This source classification does not change publication's existing
1024-based output resizing or ZIP policy. File contents are decoded and checked;
a correct extension alone is insufficient.
The existing publication minimum remains 1024 pixels: a 1K source of 1000–1023
pixels fits the source naming rule, but cannot be packaged by the current exporter.

| Maps | File format | Color and depth |
| --- | --- | --- |
| COL | JPEG; legacy TIFF with warning | RGB, 8 bits per channel |
| DIFF, NRM, SPEC, SPECLVL, SSS, SSSABSORB, TRANSL, ANISO | JPEG | RGB, 8 bits per channel |
| ROUGH, GLOSS, DISP, SHEENGLOSS, OPAC, AO, METAL | JPEG | Grayscale, 8 bits |
| NRM16 | PNG | RGB or RGBA, 16 bits per channel |
| DISP16 | TIFF | Grayscale, 16 bits |
| ID | JPEG or PNG | JPEG: grayscale or RGB, 8 bits; PNG: grayscale, 8 bits, no alpha |
| SHEEN | PNG | Grayscale or RGB, 8 bits per channel, no alpha |

**ID** is the mask token. `MASK` is not substituted or renamed automatically.
ID permits gray pixels at antialiased boundaries; this check does not require
every pixel to be exactly black or white. SHEEN permits both grayscale and color.
Valid RGB8 COL TIFF files (`.tif` or `.tiff`, matching decoded content) produce a
nonblocking `COL_TIFF_LEGACY` warning. A material with only that warning is **OK**.
The warning tells authors to use JPEG for new materials. No TIFF cutoff date or
new-material rejection is inferred from the separate ZIP policy; a hard rule for
new materials awaits a defined age/provenance boundary.
Bit depth means bits per channel, not total bits per pixel: RGB 8-bit has 24 bits
per pixel, and RGB 16-bit has 48.

### Previews and metadata

`PREVIEW` must contain `SPHERE_1.png` or `FABRIC_1.png`. The match is exact;
`SPHERE_10.png` does not satisfy it. Every preview image must be a readable PNG
of exactly **1200 × 1200** pixels. The checker also validates the source
`metadata.json` and its required values. It does not generate missing previews,
repair metadata, rename files or generate lower-resolution source folders.

The database stores the status, last inspection time, report, profile version and
whether the configured validation completed. Changes that invalidate material
source review also clear this derived result. The immutable activity record keeps
the earlier observation. A new inspection checks the current map, preview and
metadata files, including changes made outside the app. It does not monitor files
continuously or detect content changes in unrelated `SOURCE` payloads. The
timestamp describes the last observation.
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
The report begins with a summary and includes defect sections **only for
materials with issues**. Materials that pass with legacy TIFF warnings appear in
a separate **Warnings — passed materials (OK)** section. Ordinary passing
materials are counted in the summary and are not repeated in the diagnostic list.
A clean selection produces a summary with no defect sections. The report contains the material identity and folder path
so the person correcting files can find them directly. Full image decoding can
take several minutes for large materials or selections; the UI keeps the check
pending until the result arrives and blocks duplicate submissions.
When launched through a packaged Windows app, Windows can redirect this folder
under `%LOCALAPPDATA%\Packages\<package>\LocalCache\Local\REAWOTE`. The report
adapter resolves and validates that fixed physical location. The report panel
hides the saved TXT path and desktop-editor status and offers **Download TXT
report** below the report. Material-source path checks are unchanged.
`open_report: false` supports integrations and automated tests without opening
an editor. The response includes
the report text even if the desktop editor or report storage is unavailable, so
the UI can offer a TXT download. Reports contain local paths and remain local.

Migration `20260928_0033` adds the five fields without reclassifying historical
validation states. Existing materials begin with **not checked**.
