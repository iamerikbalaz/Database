# Automatic file check

The materials table displays a server-produced field independent of human
**Checked** and the older technical validation/approval records. Users cannot
PATCH this field. The material card and the explicitly selected bulk operation
run the same inspection and append the actor to material activity.

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

All maps must match the COL width and height. The longest side is exactly the
folder's K value multiplied by 1024, so a `4K` source has a longest side of 4096
pixels. Rectangular images are allowed. File contents are decoded and checked;
a correct extension alone is insufficient.

| Maps | File format | Color and depth |
| --- | --- | --- |
| COL, DIFF, NRM, SPEC, SPECLVL, SSS, SSSABSORB, TRANSL, ANISO | JPEG | RGB, 8 bits per channel |
| ROUGH, GLOSS, DISP, SHEENGLOSS, OPAC, AO, METAL | JPEG | Grayscale, 8 bits |
| NRM16 | PNG | RGB or RGBA, 16 bits per channel |
| DISP16 | TIFF | Grayscale, 16 bits |
| ID | PNG | Grayscale, 8 bits, no alpha |
| SHEEN | PNG | Grayscale or RGB, 8 bits per channel, no alpha |

**ID** is the mask token. `MASK` is not substituted or renamed automatically.
ID permits gray pixels at antialiased boundaries; this check does not require
every pixel to be exactly black or white. SHEEN permits both grayscale and color.
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
The report begins with a summary and includes material sections **only for
materials with issues**. Passing materials are counted in the summary and are
not repeated in the diagnostic list. A clean selection produces a summary with
no defect sections. The report contains the material identity and folder path
so the person correcting files can find them directly. Full image decoding can
take several minutes for large materials or selections; the UI keeps the check
pending until the result arrives and blocks duplicate submissions.
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
