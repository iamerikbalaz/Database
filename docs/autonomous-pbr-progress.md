# Autonomous PBR completion

## Latest checkpoint (2026-10-05, batch AI JSON and deletion confirmation)

The owned test at `http://127.0.0.1:53033` serves the new version. Schema remains
0044; no migration, source-file change or external AI request was required.

- Materials list/gallery now offers **AI descriptions** for 1–100 selected
  records. A self-contained JSON brief carries research instructions, public
  context, exact result template and schema. The reviewed JSON import adopts
  only explicitly selected descriptions, with separate overwrite consent.
- Stale/uncertain/missing results are blocked. Credits, tags and memberships are
  preserved. Unverified citations and human authorship are retained in immutable
  content history. Exact request receipts survive later edits/archive/deletion;
  a lost response stops the remaining saves until the same request is recovered.
- Deletion automatically checks selected records/options and offers one
  **Delete materials** confirmation. Independent padded responsive dialog styles
  and record cards fix the crowded review table. Recovery checks are retained.
- Verification: all **1,360 frontend tests**, lint and production build passed;
  **16 new backend tests**, **73 adjacent tests**, and the real migrated
  **PostgreSQL round-trip test** passed. The dashboard lazy-preview test now
  waits for observer initialization instead of racing the effect.
- Live acceptance exported/reviewed all **51 materials** successfully and
  confirmed every material/content response unchanged. All 31 existing
  authenticated page/API smoke checks passed. No live description was applied
  and no material was deleted. Visual browser interaction was not automated.

See [the Materials workflow](material-table.md#ai-description-handoff-2026-10-05).
External AI research remains user-driven: download the brief, run it in an AI
tool, then import its results. The application does not call a provider itself.

## Previous checkpoint (2026-10-05, Checked recovery and Order folders)

The owned test at `http://127.0.0.1:53033` has been restarted with these fixes;
schema remains 0044 and no data migration was needed.

- Checked Correction still returns Status to In progress. Returning to OK needs
  Done first; list/card failures now explain this requirement. No source edit is
  required to complete the review again.
- Local Done/preflight now uses the explicitly injected desktop library instead
  of the disconnected remote worker. It reads immediate resolution directories
  and bounded metadata without reading texture payloads or granting Auto-check
  OK. JSON takes precedence over legacy TXT; missing/invalid metadata remains
  advisory, while unsafe folders or missing resolution directories block Done.
- Order folder opening now handles Windows resolving mapped R: paths to UNC.
  Its separate read-only verifier pins the ancestor chain and refuses reparse
  points/redirection. Shared filesystem mutation restrictions are unchanged.
- Materials and Orders render selection checkbox followed by the folder icon,
  with corrected CSS specificity and horizontal alignment.
- Verification: 71 local preflight/material workflow tests, 17 Order desktop
  tests and 44 focused frontend tests passed; frontend lint/build passed.
  Live preflight changed from HTTP 503 to HTTP 200 with matching identity and
  valid metadata. Fresh assets and all 31 live endpoint/page checks passed;
  all 51 material records were unchanged. One real R: Order folder passed the
  verifier with Explorer mocked. Actual Explorer rendering/browser interaction
  was not automated. No NAS/source writes were performed.

AI batch prompt export and reviewed JSON import were recommended here and are
implemented in the latest checkpoint above. Source research remains external.

## Previous checkpoint (2026-10-05, material drafts and administrator deletion)

The owned test at `http://127.0.0.1:53033` serves schema 0044 and the new UI.
Its fresh pre-upgrade database dump is retained privately. The 0042→0044 upgrade
passed schema comparison and preserved every existing material field and all
existing table counts: 51 materials, 303 customers and 283 orders.

- Material property columns, colored Status/Checked and Notion-derived Order
  Status/Priority colors; direct folder icons in Material and Order rows.
- Names-only single/bulk creation keeps missing facts NULL. Customer/category
  completion reserves real identities; recoverable folder creation is separate.
  Draft Edit Name works directly on the card with durable request recovery.
- ADMIN-only reviewed bulk deletion removes records from active/archive views,
  with either unchanged sources or whole source folders moved into a protected
  recovery journal. Files are retained, not permanently purged. Immutable audit,
  historical identity reservations and interrupted-operation recovery remain.
- Full frontend: 1,305 tests, lint/build passed; final draft-name addition: 34
  focused tests, lint/build passed. Affected backend API/native and PostgreSQL
  checks passed, including real concurrent allocation and deletion. Receipt
  trigger contracts were updated for the two new material fields; older receipt
  JSON remains readable. No worker code changed.
- Live acceptance: 31 existing endpoint/page checks plus 10 new API/asset checks
  passed, without material or source mutations. Native Explorer launch was
  adapter-tested; browser rendering and physical interaction remain unverified.
- Broader unrelated full-backend runs were stopped before completion and are
  not counted as passed; see the feature note for the targeted test coverage.

See [behavior, safety and verification](material-drafts-deletion-2026-10-05.md).

## Previous checkpoint (2026-10-01, compact cards and full-quality previews)

The owned test at `http://127.0.0.1:53033` now serves compact material,
Order and Customer cards, full-quality preview browsing and native Paths pickers.
Schema remains 0042. No source changes or NAS copies were performed.

- Material panels align in height; library fields use compact rows; controls are
  grouped by type. Keep filters follows Add in database headings. Order paths use
  a compact copy icon. Horizontal thumb-wheel and Shift+wheel synchronize tables.
- Full-quality previews load original PNG/JPEG/WebP bytes on demand, or lossless
  TIFF-to-PNG with ICC and 16-bit preservation, through authorized read endpoints.
- Paths adds the published-library destination on Z:, editable even when offline.
  The future snapshot layout excludes SOURCE; copying is not implemented here.
- Full frontend: 1,259 tests, lint and build passed. Preview backend/native Windows
  99 tests and actual Linux worker 74 tests passed. Paths backend 59 passed,
  1 Windows symlink privilege skip. No migration was required.
- Live acceptance verified 51 records, original 1200 x 1200 PNG byte equality,
  unchanged source hash/mtime, thumbnails, Paths and the fresh built assets.
  Browser visual inspection and physical MX Master testing remain unverified.

See [behavior, verification and rollback](compact-cards-original-previews-2026-10-01.md).

## Previous checkpoint (2026-10-01, material card controls and bulk names)

The local test at `http://127.0.0.1:53033` serves the refreshed material card and
Edit names action in the selected-material toolbar for list and gallery views.
Schema remains 0042; source files were not changed during verification.

- Keep filters text/checkbox placement and collapsed Preview width corrected.
- Bulk literal name replacement reviews source plans and confirms existing durable
  identity operations; stale records, unknown outcomes and recovery are covered.
- Card refresh icons, compact preview navigation, aligned panel headings and
  side-by-side folder/library layout; Check material data is in the page heading
  and its report appears after execution and survives the subsequent refresh.
- Full frontend 1,230 tests, lint and build passed. Live API/asset acceptance
  passed. Remote main remains unchanged at `88a1f99`.

See [behavior and verification](material-card-and-bulk-names-2026-10-01.md).

## Previous checkpoint (2026-10-01, compact materials and preview file editing)

The owned local test at `http://127.0.0.1:53033` runs schema 0042. Its fresh
database backup and read-only live API acceptance passed; all 51 material records
and their source files were retained during deployment.

- Shared filter-label placement and independent Sort controls/defaults across
  Materials, Orders, Customers and Catalog. Optional material Number follows Preview.
- Expandable preview strips, confirmed single/bulk source PNG edits, durable
  recovery and quarantine; selection-only bulk controls and Main category changes.
- Compact material detail/Create/category choices; new folders contain PREVIEW,
  SOURCE and SBS, with no generated XK folder. Old batch recovery remains compatible.
- Full frontend: 1,205 tests, lint and build. Focused backend/native Windows:
  creation 31, preview edits 28, shared identity/metadata/table guards 112 tests.
  Four isolated PostgreSQL migration/ownership/recovery tests also passed.

See [behavior, verification and limits](material-previews-iteration-2026-10-01.md).

## Previous checkpoint (2026-10-01, creation, categories and offline exports)

The owned local test at `http://127.0.0.1:53033` runs schema 0041 on the existing
`codex/customer-scroll-pilot` branch. No merge to main was performed.

- Shared sorting/Keep filters; catalog edit dialogs and reserved historical codes;
  Orders defaults, folder creation and material progress; optional Order with
  enforced Customer consistency; batch material/SBS folder creation; additive
  categories/collections; and Settings Paths are implemented.
- Customer Published and the five-column brand CSV export are available. The new
  app status labels are migrated. Published was added to Notion and set for 144
  verified public-brand matches in both systems. Renaming the three existing
  Notion Status options still requires Notion's UI, and ongoing outbound delivery
  still requires the separate application integration token.
- ZIP uses original 1024-per-K rules, independently of source-check thousands
  intervals. Material CSV excludes brand collections. The rebuilt worker passed
  1,056 tests; a real 4K source produced verified 4K/2K/1K archives and CSV from an
  isolated clone without source changes. No upload or automatic Published change
  was performed. Existing live materials still need their publication drafts and
  credits before actual export.
- Final frontend: 1,182 tests, lint and build passed. Broad backend/PG regressions
  were repaired and rerun; final category/creation/head checks passed 68 tests.
  Fresh clone/live migrations and live API acceptance passed. Backups are retained
  in the ignored iteration runtime directory. Existing 51 material records and
  source folders were preserved.

See [iteration details, verification and limitations](iteration-2026-10-01.md).

## Previous checkpoint (2026-09-30, Customer identity / shared database UI)

The owned local test at `http://127.0.0.1:53033` now runs schema 0036.
Remote main was verified unchanged at `88a1f99d748d2a0edbb1fce509e13d18bfc03908`.

- Customer names change only through a confirmed dialog, including the legacy API
  guard. Future materials use the new prefix. Opt-in historical changes use durable
  per-material filesystem journals, preserve IDs/counters, update metadata, and set
  successfully renamed materials unpublished. Retry/recovery and terminal partial
  outcomes remain explicit. Historical prefix/name ownership stays reserved.
- Created/Updated ranges, shared top horizontal scrolling and page-level vertical
  scrolling, unified selected-row bulk controls, Settings destinations, and mobile
  menu dismissal are implemented. Materials/archive filter out unassigned inactive
  processor duplicates while preserving historical filter choices.
- Live data work: 124 public REAWOTE logos matched unambiguously; seven active
  processors reconciled with approved People aliases. Former staff and historical
  accounts were retained inactive. Fifteen same-person material assignments were
  consolidated, preserving manual/automatic file-check results with audit entries.
  Source paths, material contents and order folders were not changed by rollout.
- Validation: 126 initial targeted backend/NTFS tests passed; broader backend run
  passed 263 scenarios with two legacy error-message regressions subsequently fixed
  and covered by a successful 47-test rerun. Nine PostgreSQL tests and five separate
  recovery tests passed. Linux worker: 63 tests, including eight same-path fault
  scenarios. Frontend: 91 targeted tests, subsequent 9 and 35 focused checks, build
  and lint passed. Fresh visual browser verification remains unavailable under the
  existing browser restriction; component/layout behavior was checked in tests.
- Clone and live 0035→0036 migrations and Alembic checks passed. Live acceptance
  confirms 303 Customers / 282 Orders / 50 Materials / 124 logos / 7 active processors,
  working date filters, API access, frontend assets and unchanged existing values
  across the migration. No real customer rename, Notion write or NAS write was used
  during acceptance. Existing file-check/publication image pins were preserved.
- Local test credentials were rotated and old sessions revoked after accidental
  diagnostic exposure. The disposable acceptance database was replaced, invalidating
  its prior credential. Replacement credentials and operational snapshots remain
  private in ignored runtime files, never in Git. The test-login file is updated.

The final pre-migration backup is `before-schema0036-apply.private.dump` in the
owned runtime directory. See [version notes](customers-orders-version.md) for
recovery, historical rename behavior and remaining Notion-token/template inputs.

## Previous checkpoint (2026-09-30, Customers / Orders)

Resumed the approved directory iteration on `codex/autonomous-pbr-completion`.
Remote main still resolves to `88a1f99d748d2a0edbb1fce509e13d18bfc03908`.
The running local test at `http://127.0.0.1:53033` is upgraded to schema 0035.

- One-level Customers and Orders replace the old directory UI. Shared tables
  support inline edits, reviewed bulk changes, Properties and Ctrl/Shift
  highlighting; Catalog no longer requires a reason. Navigation has distinct icons.
- Orders use the supplied uppercase naming formula, explicit folder-rename
  confirmation, durable new-folder creation under `R:\0. PROJECTS`, and profile
  properties matching the approved Notion schema. Customer categories derive
  from assigned materials; logo upload is available.
- One-time bootstrap completed: 303 Customers, 282 Orders, 50 unchanged materials,
  and all 252 original order folder paths preserved. Ambiguous matches remain
  separate/unlinked. Existing manufacturer names used by metadata.json remain
  unchanged. Exact source data and conflicts are private, outside Git.
- Ongoing Notion writes are one-way, durable and currently **disabled** because
  the application integration token is missing. Historical inbound adoption is
  retired (410); historical receipts remain readable. No real Notion or NAS
  writes were used for acceptance. New user-created orders can now create folders.
- Frontend: 1,108 tests, build and lint passed; final focused corrections 20/20.
  Backend: 110 targeted tests and 41 outbound/API/migration tests passed, with
  one Windows symlink privilege skip. PostgreSQL: 343 initial passes; all 24
  stale fixture/retired-feature failures corrected and rerun successfully;
  8 additional new concurrency/migration checks passed. Auth gate 27/27.
  Clone and live test-instance API/upgrade acceptance passed. Fresh browser
  visual/E2E verification remains unavailable under the existing browser denial.

See [version notes and rollback](customers-orders-version.md) and
[outbound integration contract](customers-orders-outbound.md). The owned runtime
retains a pre-migration dump. Source material and publication-worker image pins
were preserved. Templates, OneDrive links and ZIP refinements remain the next
user-directed iterations. Current remaining inputs: Notion application token and
decisions on one conflicting customer identifier and two duplicate order numbers.

## Previous checkpoint (2026-09-30, automatic-check filters and source rules)

Continued the existing `codex/autonomous-pbr-completion` branch. This slice adds
the Materials **Automatic check** filter (not checked / OK / issues) across the
active list, gallery and archive. It composes with existing filters and access
scope. The Color control now fits narrow filter tracks without touching Project.
The report panel shows the downloadable TXT without a saved-path field or editor
status message.

ID maps additionally accept RGB/grayscale JPEG8. Source resolution folders now
represent complete thousands of pixels on the longest side: 8K accepts
8000–8999, including 8600×8192. All maps must still match COL dimensions. COL RGB8
TIFF is accepted with a nonblocking `COL_TIFF_LEGACY` warning; warning-only
materials remain OK and appear separately in the bulk report. Warnings cross the
bounded native/API contracts and are retained in material audit evidence.

The cutoff distinguishing old from new materials is not yet defined. TIFF
therefore warns authors to use JPEG for new materials; it is not rejected based
on an invented date or the unrelated ZIP policy. Publication retains its existing
1024-based output sizing, including the 1024px minimum (a 1000–1023px source can
fit a 1K folder yet remain too small to package). The legacy technical approval
contract retains that minimum as well.

### Verification

- Backend/API/native result parsing: **189 passed**, including status filters,
  archive/access scope, warning-only OK/report/audit, invalid-warning rejection,
  existing check jobs and material operations.
- Frontend: **67 focused tests passed**; build and lint passed. Browser visual
  verification remains unavailable after the browser tool rejected access to the
  local tab; responsive sizing was inspected in CSS and component tests passed.
- Linux worker: **269 focused tests passed**, followed by **14 final regression
  cases** on the final source. Includes the exact 8600×8192 example, interval
  boundaries, TIFF/JPEG ID real ZIP output, legacy approval minimum and TIFF
  cache reuse. Final test image:
  `sha256:8b80cffd8d4631c1aed2908233df968274389f9e323526f539ae75a2dd4ad1ef`.
- Automatic-check runtime updated to
  `sha256:cf1bf7c7965aff69db2c9caf70f43e60b3429f1cd0d87885c6c0bf394e57ee75`.
  The server restarted successfully and `/health` reports database connected.
- The proposed joint update of `publication-image.txt` was rejected by automatic
  approval review as outside this check-focused request. The safer deployment
  updates **only** `file-check-image.txt`; publication retains
  `sha256:b43d0b49565e2ce7b84dfbd7d687a36c2219fe366c5654ce1019005addabd91a`.
  Shared-rule export code is tested and ready, but applying that runtime update
  awaits user approval. Until then export can still reject these newly accepted
  source formats/dimensions under its older preflight rules.
- Full 50-material API acceptance passed in **326.0 seconds**: **35 OK / 15
  issues**. All three active and archive filter values returned exactly the
  expected IDs (the current dataset has no archived materials). All 990 source
  file sizes/mtimes and all other material DTO properties remained unchanged.
  Report persistence matched the API response. This real set contains no COL
  TIFF warnings; TIFF behavior is covered by the synthetic decode/export tests.
  The new image's first run inspected 460 images and reused 155 facts. Evidence:
  `tmp/local-materials-v5-20260928/v10-summary.json`, `v10-job.private.json`
  and `v10-before.private.json`. No source repair was performed.

## Previous checkpoint (2026-09-29, faster automatic checks and live progress)

Continued `codex/autonomous-pbr-completion`; a fresh fetch confirms `origin/main`
remains `88a1f99d748d2a0edbb1fce509e13d18bfc03908`. No main merge or original NAS
mutation. The app remains on `http://127.0.0.1:53033`, using only `Test_data`.

### Delivered

- Automatic checking copies inputs while hashing into a private Linux staging
  directory and decodes that copy. The final source hash remains mandatory.
- Authenticated image facts are reused by content SHA, immutable image and
  evidence version. Current names, required maps, metadata and cross-map rules
  are always checked again. Corrupt or incompatible cache entries are misses.
- At most two isolated material processes run within 2 CPU / 6 GiB. Staging is
  bounded to 8 GiB per material and removed after use. The bounded cache stores
  image facts, not material approvals or image payloads. Source protection and
  publication's full inventory/proof remain unchanged.
- Card, bulk and optional publication checks show completed counts, active
  files, elapsed time and cache reuse. Resumable local jobs avoid repeating a
  scan after a lost response. Results are owner-only and each poll reauthorizes
  the whole selection. Only complete checks commit results atomically.
- The optimized checker uses its own runtime image configuration. The local
  publication export still uses the previously verified publication image.

### Verification

- Worker: **195 targeted tests passed** across inventory, image validation,
  strict checking, local command and export contracts. The final cache/staging/
  concurrency/progress suite passed **33 tests**. A supplementary whole-worker
  run was deliberately stopped after at least 468 successful tests; it is not
  reported as a complete suite pass.
- Backend/API/access: **106 passed**; both later retention/eviction cases passed.
  Native Windows bridge: **65 passed**, with the two final exceptional-cleanup
  cases checked again. Frontend: **1,090 passed**, build and lint passed.
- The separate native smoke checked ROUBAL and a smaller 4K material twice.
  Findings matched the previous version, both workers and progress were observed,
  and the second pass reused all 24 image facts without a decoder miss.
- Full 50-material API check, initially empty evidence cache: **293.6 seconds**
  (4m54s), compared with the previous approximately 18-minute run. The 615 image
  inspections included 459 fresh decodes and 156 reuses within this first batch.
  All issue lists, statuses and completeness flags exactly matched the baseline:
  **23 OK, 27 issues**. All 990 file sizes/mtimes and all other material DTO values
  were unchanged. The report still contains only the 27 defective materials.
- Immediate repeat of the same 50 materials: **123.2 seconds** (2m03s), with all
  615 image inspections served from authenticated evidence and no decoder misses.
  All findings again exactly matched the baseline; source files and other record
  values stayed unchanged. Both full API runs observed two active materials,
  live progress, report persistence and same-key completed-job recovery. These
  timings are observations on this local Test_data machine, not a NAS guarantee.
- Evidence is retained outside Git in `tmp/local-materials-v5-20260928/`
  (`v9-cold-summary.json`, `v9-warm-summary.json`, job/progress/before private snapshots), and in the
  fixed local report directory. No source repair was performed.

## Previous checkpoint (2026-09-29, complete automatic material checks)

Continued the existing `codex/autonomous-pbr-completion` branch. Fresh fetch still
shows `origin/main` at `88a1f99d748d2a0edbb1fce509e13d18bfc03908`. The complete
checker and performance fixes are committed and pushed as `6f650e1` and
`aa243d3`. Final real-data verification completed successfully.

### Delivered behavior

- `PBR_FILES_V1` evaluates the agreed required COL/ROUGH/NRM set, all 20 map
  types, encoded format/depth/channels, uppercase identity and map filenames,
  exact master dimensions, PNG 1200-square previews and root metadata.json.
  ID is grayscale PNG8; SHEEN accepts grayscale or RGB PNG8. NRM16 and GLOSS
  cannot substitute for the required NRM and ROUGH maps.
- Card, bulk selection and local export use the same rules. A completed check
  records OK or issues with actor/time/history, independently of human Checked.
  The bulk TXT has a summary and details only for defective materials.
- One isolated, network-free container checks the complete selection. Native
  Windows handles protect source files; stale permissions/versions, incomplete
  worker output or source races cannot write a partial or false-OK result.
- Historical BASIC_V1 results and legacy technical approval evidence retain
  their original meaning. No migration or retrospective human approval is added.

### Verification and local runtime

- Initial complete worker/packaging suite: **962 passed**, including 70 strict
  check and 8 strict offline-export cases. Color SHEEN survives actual ZIP
  conversion; existing A/B layout, timing and resizing policy remain unchanged.
- Frontend: **51 focused tests**, build and lint passed. API/access checks,
  map/packaging contracts, local-export regressions and native Windows source
  protection tests passed. PostgreSQL: **4 passed** in a fresh disposable owned
  instance, including full-OK table edits/replays and forged-receipt rejection.
- The first live 50-material batch failed closed: no partial database results
  were stored. An isolated full-tree scan of a 2.49 GiB material took 101.2s.
  The final optimization hashes automatic-check inputs only (resolution folders,
  PREVIEW and root metadata), excludes unrelated authoring/SOURCE payloads, and
  removes a redundant verification pass. Publication keeps full-tree evidence.
  The narrowed scope is explicitly hash-bound and cannot serve as export proof.
  These scope changes passed **218 targeted Linux tests**. A subsequent real-data
  run isolated a separate decoder wall limit: the 321 MB ROUBAL NRM16 PNG timed
  out after 35.048s while consuming only 11.647s of CPU. The strict profile's
  map decoder now allows up to 120s wall time, clipped by the remaining material
  deadline; CPU/memory and the legacy decoder limits remain unchanged. The final
  image passed **199 targeted tests** (strict checks, technical validation, local
  export, conversion and assembly). The real ROUBAL check completed in **66.6s**,
  reporting its ID map stored as JPG instead of the required PNG.
- The local application remains `http://127.0.0.1:53033`, rooted exclusively at
  `C:\Users\Admin\Desktop\Test_data`. The final 50-material API batch completed
  in approximately 18 minutes: **23 OK, 27 issues**. All 50 persisted results
  were independently read back and verified as complete `PBR_FILES_V1` results.
  The saved TXT matches the API report and contains exactly the 27 defective
  material sections, with passing materials only in the summary.
- Native before/after source guards completed for every selected material.
  A supplementary card check on a small 4K material completed in 7s and preserved
  all other record fields and archive state; a snapshot comparison confirmed the
  990 source files retained their sizes and modification times through that check.
  The original bulk harness had an assertion against an omitted active-record
  archive default after its successful API call; the corrected readback/card
  verification ran separately without repeating the expensive bulk scan.
- Typical actual findings: 23 materials have format/depth/channel or extension
  mismatches, 9 have missing/invalid sample dimensions, 5 have master dimension
  mismatches, and 5 lack the primary preview. These categories overlap. No repair
  or source conversion was performed. The report and acceptance summary are
  retained locally, outside version control, in the configured reports directory
  and `tmp/local-materials-v5-20260928/v8-check-acceptance-summary.json`.
- Docker initially failed to start because of inaccessible stale AF_UNIX socket
  files. Only its transient socket directories were moved to retained sibling
  backups, allowing a fresh start; no Docker database or volume was reset.

See [the complete rules](automatic-file-check.md) and
[offline publication](offline-publication.md) for the operational contracts.

## Previous checkpoint (2026-09-28, Materials selection and offline publication)

Continued `codex/autonomous-pbr-completion` from `401a23c`. A fresh fetch confirmed
`origin/main` is still `88a1f99d748d2a0edbb1fce509e13d18bfc03908`. Changes stay on
the development branch; no main merge, production database or original NAS writes.

### Delivered

- Materials has a named, swatch-based multi-color filter using the 18 REAWOTE
  platform HEX values. Colors are ORed together and combined with other filters.
- List rows support click, Shift range, Ctrl/Cmd toggle and Ctrl+Shift additive
  range highlighting. **Select highlighted** adds those rows to checkbox
  selection. Interactive cells retain their editing behavior. Selection survives
  switching between list and gallery; changing filters clears the selection.
- Read-only **Automatic file check** replaces File check. Card and selected bulk
  checks record the actor, timestamp, observations and profile; source changes
  invalidate the result. See [automatic-file-check.md](automatic-file-check.md).
  Current BASIC_V1 rules are preliminary: observed defects show **issues**,
  otherwise **not checked** remains until complete rules are agreed. **OK** is
  reserved for that complete profile. Human Checked remains independent.
- **Prepare selected for publication** replaces the filtered-publication and
  publication-batches actions in Materials. The selected workflow offers optional
  automatic checking, **Review materials**, then **Prepare publication** with a
  native destination picker. It creates a unique subfolder with CSV, all ZIPs
  and a receipt, then asks whether to mark Published. Upload remains manual.
- Offline export uses the existing Linux packaging engine and global A/B policy.
  Historical DIFF/METAL/SPEC/ID/MASK maps are retained. Frozen input, idempotent
  requests, final authorization/version checks and source hash checks protect
  export and the later all-or-none Published confirmation. See
  [offline-publication.md](offline-publication.md).

### Local runtime and provenance

The 50-copy instance remains at `http://127.0.0.1:53033/materials`. An owned
database backup precedes migration **20260928_0033**. Its receipt guard preserves
both historical and intermediate receipts without rewriting their JSON/hash;
normal edits exclude full potentially large reports. The current guard was
forward-applied and Alembic/schema drift checked on this test database.

The runtime pins the tested immutable packaging image. A persisted map binds
**49 material UUIDs** to original master-folder timestamps from the verified
pre-copy inventory, so renaming/copying does not choose the wrong ZIP policy.
One material contains SOURCE only and has no master timestamp to capture. The
map is loaded on restart rather than recalculated from current directory times.
Source files are not modified by inspection or export.

The active test records do not yet have the Credits values needed for publication.
Review reports the missing values. Export acceptance used a disposable database
clone with explicitly synthetic publication content; these values and Published
flags were not written into the active 50-record database.

### Verification

- Frontend: **1067 tests passed**, lint, TypeScript and production build passed;
  **51 final focused tests** passed after the last UI/report corrections.
- Broad Windows backend run: **2442 cases passed across the partitioned run and
  corrected migration-head rerun**; 483 environment-dependent cases skipped in
  that run. The stale expected migration head was corrected, then its module,
  Alembic and automatic-check tests passed together (**68 cases**).
- Dedicated PostgreSQL runs: **10 receipt/migration/concurrency cases**, **17
  automatic-check API cases**, **11 publication API cases**, and **27 auth cases**
  passed without selected skips. Large reports, old receipt replay, timestamp
  normalization and rejected forged derived fields are covered.
- Offline backend/native: **28 API/Windows tests**, **101 contract tests** and
  **65 Linux packaging/validation cases** passed. The Linux count includes the
  full run and one corrected test-fixture rerun.
- Actual export produced a one-row CSV and **4K/2K/1K ZIPs** from the Terratinta
  copy. All 13 source files remained identical. Original 2024 provenance selected
  method A and every ZIP entry had the expected 1 January 2026 timestamp.
  Published confirmation and exact replay passed only in the disposable clone.
- End-to-end: **25 fresh + 25 after restart** passed on final migration code.
  Legacy publication checks remain covered via its existing route and explicit
  selection. Owned test containers/networks were cleaned up.
- Browser inspection verified multi-color filtering, Shift/Ctrl highlighting,
  checkbox selection across list/gallery, and real missing-content review.
  A real two-material bulk check saved its BOM-encoded TXT and launched Notepad;
  the UI displayed the exact saved path. Windows MSIX report redirection was
  corrected without weakening source-path checks; **11 native report tests**
  passed, including refusal of a real junction to an unrelated directory.
  Native folder-picker interaction is covered by adapter tests; final Windows
  export acceptance uses the same native destination-grant boundary.

Final automatic map-validation rules remain the next iteration. Private export
job artifacts are retained for diagnosis; automatic cleanup is not implemented.

## Previous checkpoint (2026-09-28, material card corrections)

Continued the existing development branch from `977ae76`. No schema migration,
main merge or production deployment is needed for these corrections.

### Delivered

- **Edit Name** now has one confirmation after the name/source-change warning.
  The server still plans and verifies the source changes internally. An
  unpublished Done material can be renamed without changing its workflow status;
  Checked and stale technical proof reset. Moving, rebranding and category changes
  retain the In-progress requirement.
  Names can be extended or shortened without the new name being mistaken for
  an old JSON reference. Unknown unmapped references still block the operation.
  Generic unlinked-record edits also cannot change a published identity.
- Named imports and newly created materials use the full uppercase material
  component. `ROUBAL_0001_TILES-ORANGE_B01` produces `TILES-ORANGE`, including
  every word; numeric names such as `20-08` are preserved too. Unlinked name edits
  update their generated identity consistently.
- Source JSON uses the full supplied production template with matching section
  order and indentation. Current measurements and extra data are preserved;
  unknown measurements remain null. Verified filename inventory fills missing
  map/resolution/source facts without reading the texture bytes. Both native and
  Linux writers retain their existing durable journal and recovery rules.
- The Color HEX combobox shows swatches both in the selection and every option,
  with keyboard navigation and custom recorded values. Preview ordering starts
  with FABRIC_1/SPHERE_1 and then follows numeric suffixes; reload restores the
  primary image.

### Copied-data correction

The existing 50-material Desktop `Test_data` instance remains at
`http://127.0.0.1:53033/materials`. An owned database backup and per-file receipts
precede the audited correction. **46 shortened names** were corrected in the
database, and **all 50 JSON documents** now have the full production shape.
Existing colors, dimensions and user workflow/Checked labels were preserved for
this structural backfill. No material folder was renamed by the backfill.
Final hash verification confirmed **all 937 original copied files unchanged**.
Private plans, backups and reports remain in the ignored local runtime directory.

An actual Windows HTTP/API acceptance test renamed one Done copy to a name with
an appended suffix and back. Both source operations completed. Done stayed Done,
Checked reset to no and was then restored through the normal audited table API.
The original name/path, all **12 original files** and exact metadata JSON bytes
were restored; the temporary destination no longer exists. The audit retains the
test operations. No direct database edit was used for this round trip.

The normal user rename/save behavior still resets Checked; the backfill's
historical-label preservation is not a new bypass for source editing.

### Verification

- Frontend full unit suite: **1012 passed**; lint, E2E TypeScript and production
  build passed. Browser inspection confirmed real swatches, complete names,
  enabled Done-name input, one confirmation and SPHERE_1/WALL_2/FLOOR_3 ordering.
- Targeted PostgreSQL identity/metadata/resource suite: **59 passed**, including
  all **27 authentication checks**, with no selected skips. The Done rename test
  covers status preservation, Checked reset, exact replay and guarded moves.
- Native Windows filesystem suite: **31 passed**, following correction of a
  directory-sharing conflict in filename-only inventory.
- Naming/import regression suite: **128 passed**; affected generic-name/history
  checks: **76 passed**. Linux metadata/identity checks: **213 passed**, followed
  by **52 passed** for the final pretty-printed identity rewrite.
- The full-template null-dimension transport fix passed **62 focused tests**.
  Final published-identity guards passed **137 tests**. The final suffix-rename
  fix passed **103 backend tests including all 31 native Windows checks**, and
  **216 Linux worker tests**, with no skips.
- Final end-to-end run on all final source changes: **25 fresh + 25 retained**
  scenarios passed. It exercises swatch selection, combined JSON/content save
  with missing dimensions, one-confirm suffix rename on Done/OK, exact replay,
  changed real filenames/JSON and retained results after service restart.
  Screenshots were visually inspected. Owned containers/networks were removed;
  synthetic evidence volumes remain. Regular/demo environments were unchanged.

No original NAS writes are part of this work. The local technical-check and
packaging boundaries from the preceding checkpoint still apply.

## Previous checkpoint (2026-09-28, material card and local file editing)

Continued `codex/autonomous-pbr-completion` from `29473b3`. A fresh fetch confirmed
`origin/main` remains `88a1f99d748d2a0edbb1fce509e13d18bfc03908`; the original
main checkout is unchanged. No schema migration, production deployment or merge.

### Delivered

- The material card combines publication content and editable metadata in
  **Material data for library**. One durable save writes root `metadata.json`
  before finalizing the content and metadata in the database. Unknown JSON fields
  and precise numbers survive edits. Missing files can be created from imported
  values; legacy text is a fallback only when JSON is absent.
- Color choices match the 18 HEX values read from the live REAWOTE texture filter.
  Existing custom values are retained. Dimensions use decimal spinboxes with a
  0.1 cm step. The main category is required and follows controlled identity
  changes; catalog changes cannot invalidate an active source-save request.
- One paginated material history combines record, content, metadata, identity,
  workflow and archive events with the recorded actor. Unknown historical authors
  stay explicitly unknown. Processor assignment is separate from change authorship.
- **Edit Name** and destination changes preview and confirm folder, matching map
  names and JSON updates. Ordinary linked-name writes are rejected. Brand renames
  that would leave linked JSON manufacturer values stale are also rejected.
- **Material data folder** shows an expandable file tree, absolute path, Explorer
  opening and a native destination picker. Previews have arrows, source filename,
  original dimensions and reload. Separate metadata/packaging/history panels are
  removed; the card ends with a provisional material-data check and report.
- Dirty library drafts block conflicting property edits and navigation until saved
  or discarded. History refreshes after content-only saves too.

### Local acceptance and source safety

An independent owned PostgreSQL instance serves **50 copied materials** from the
approved Desktop `Test_data` directory at `http://127.0.0.1:53033/materials`.
The previous R100 instance remains separate. The private bootstrap, restart script,
credentials, backups, manifests and test guide are in the ignored
`tmp/local-materials-v5-20260928` directory; none belongs in Git.

Created **50 metadata.json files** from imported values. Verified SHA-256 for all
**937 original copied files** after creation; original NAS data was not changed.
The set spans 18 brands and 7 categories; 46 materials have previews and 4 do not.
A real HTTP/PostgreSQL/NTFS acceptance round trip saved JSON and content, retried
the exact request, verified authorship, moved a material and moved it back, restored
the original values and rechecked original file hashes. An ordinary Note edit and
restore also verified replayable resource history in the copied database.

The opt-in Windows adapter holds and verifies file handles, rejects unsafe links,
uses no-replace renames and retains persistent recovery receipts/backups outside
the source tree. It is bound to this loopback instance and local library only.
See [desktop library](desktop-material-library.md),
[metadata contract](editable-source-metadata.md) and
[identity changes](identity-operations.md).

### Verification

- Frontend full suite: **1001 passed**. Subsequent dirty-draft/history focused
  checks: **56 passed**; final lint and production build pass.
- E2E: **25 fresh + 25 retained**, including dirty-draft protection, source save,
  previews and publication/ZIP regression checks. Owned test containers/networks
  cleaned; volumes and diagnostics retained.
- Linux worker: **876 passed, 0 skipped**, with real ImageMagick/ZIP enabled.
- Backend non-PostgreSQL full run: **2320 passed, 12 failed**. One AI-adoption
  provenance regression was fixed; eleven old mutation probes now use permitted
  processor changes instead of bypassing confirmed source renames. A fresh
  follow-up ran **all 12 former failures successfully**. New identity-value and
  catalog-reservation regressions are included in the focused counts below.
- PostgreSQL full run: **460 passed, 20 failed** on previous mutation/category
  assumptions. Updated tests use permitted project/processor changes without
  weakening locks or source guards. Follow-up **93 passed**, covering former
  failures plus source/identity/concurrency tests and all **27 auth checks** with
  no skips. Fresh/historical migrations and schema checks passed in the full run.
- Native Windows/API/metadata focused suite: **100 passed**. Preview contracts:
  **45 passed**; category/source reservation checks: **56 passed**. Identity
  completion retains display values while clearing stale source proof: **27 passed**.
- Live browser: combined editor, required category, preview arrows/resolution,
  file tree/path, author history and basic check report verified. Explorer/native
  picker launch is covered by API tests, not an interactive OS-dialog acceptance.

### Remaining boundaries and restart

The basic check currently inspects files, metadata and preview availability. Final
map/ZIP validation rules await the next product decision. The local adapter does
not replace production inventory/technical/packaging workers: local50 external
publishing and packaging are disabled. Final NAS acceptance still needs a correctly
mounted worker. AI CSV exchange and live Notion/GCS remain deferred.

Confirmed identity operations retain ADMIN/PRODUCTION_LEAD, unpublished and
In-progress requirements. Destination parents must be inside the approved root.
Display-only capitalization changes that normalize to the same technical identity
and coordinated multi-material brand renames are not implemented.

Use the ignored local50 `start-test.ps1` to start the existing instance, or
`start-test.ps1 -Restart` to load code changes without resetting data. Keep its
journal and database volume. No migration rollback is needed for this version;
do not roll back source-changing operations by manually deleting journal entries.
Preserve the source/database pair and restore backups into an isolated instance
if an operator needs to investigate recovery.

## Previous checkpoint (2026-09-27, material properties and source metadata)

Continued `codex/autonomous-pbr-completion` from `00f4467`. A fresh remote read
confirmed main remains `88a1f99d748d2a0edbb1fce509e13d18bfc03908`; main checkout
is clean and unchanged. No removed auth branch was used.

### Delivered

- Archived is a list/detail/bulk checkbox; Archive shares Materials filters,
  table/gallery and Properties. Archive/restore preserve Status, Checked, Published
  and current metadata while invalidating technical review. Existing ownership
  and publication fences remain. No reason field is presented.
- Detail reuses list property controls and exposes safe folder browsing and
  root metadata.txt editing/creation. Source writes use a durable operation,
  exact replay, hash checks, guarded Linux descriptors and an atomic journal.
- Source review, AI, content approval, metadata snapshot history and individual
  ZIP-policy panels are hidden. Publication content reason is optional; existing
  explicit-reason receipts retain their original request hashes.
- Global Settings selects ZIP A/B from the original master date and configured
  cutoff/timezone. New batches freeze that version automatically; existing
  artifacts retain their policy. Technical checks remain, separate human
  approvals are no longer a phase-one prerequisite.
- Excel import supports HEX, cm dimensions, Done, Checked, Note and brand
  identifier. Invalid units/conflicting statuses are rejected rather than guessed.
  Imported metadata retains unverified provenance and causes no source write.
- Company creation/rename, brand rename/transfer and Notion company-name adoption
  maintain a same-name company brand within the same transaction.

See [version details](materials-v4.md), [editable metadata](editable-source-metadata.md)
and [packaging policy](packaging-policy.md).

### Actual acceptance instance

Backed up the owned database before migrations 0030–0032; all prior table rows
were unchanged by migration and `alembic check` found no drift. Keep the private
backup and owned volumes. Populated new history has downgrade guards: use a
forward fix or restore the backup into a separate owned instance, never force
downgrade or overwrite original/demo volumes.

Enriched the 100 selected historical records: 97 Done, 90 Checked OK, 99 colors,
84 dimensions, 63 notes including preserved manual edits. Eighteen brand
identifiers now match Excel; four manually changed fields and the existing archive
flag were retained. One explicit `-m` size awaits unit clarification. All 111
companies now have a same-name brand; 99 were added. There are 118 total brands,
252 projects and 76 categories. No source folders were created or renamed.

All 100 selected R folders and 280 preview filenames can be listed through a private
read-only Windows adapter. It holds ancestor handles against rename, rejects
reparse points and bounds/rechecks listings. No original source file was modified.
Docker Desktop cannot bind this mapped R share, so real R metadata writes and
technical checks remain blocked pending a correctly connected worker.

The synthetic worker can write only one exact Linux-volume metadata fixture;
its surrounding Windows source bind is read-only. Create/edit/replay, unknown-key
preservation, mode 0644 and unchanged texture/preview hashes passed. An earlier
Windows-bind fixture retains its pending journal because the filesystem rejected
atomic no-replace rename; no ownership guard was bypassed. The private guide links
to the working fixture. Current total: 103 materials, 102 active and 1 archived.
ZIP/CSV stays testable with the separate synthetic fixture. Notion/GCS/upload
remain disabled; original/demo databases and the Y library remain untouched.

### Verification

- Frontend: **1008/1008**, lint and build pass; main JS 477.75 kB.
- E2E: **25 fresh + 25 retained**, run `dd4fe402-0f39-4295-ab7d-7f5fa339bff6`,
  project `reawote-e2e-material-v4-final-0927`; real ZIP/CSV and restart checks pass.
  Owned containers/networks cleaned, test volumes/artifacts retained; protected
  original/demo state unchanged.
- PostgreSQL: full run 472 passed / 8 outdated test assumptions failed. Fixed the
  access-lock observer, expected guard message, extra company-brand audit event
  and legacy migration fixtures; no production guard relaxed. Follow-up **67/67**
  includes all former failures, all 27 auth checks with zero skips, lifecycle and
  new settings/metadata cases. All 480 distinct integration cases are covered.
- Linux backend: 2194 passed,480 PostgreSQL cases skipped, one archive expectation
  collected before its update failed. Updated archive suite 36/36 and exact failed
  case rerun pass. Import/company/resource/Notion follow-up 104/104; content reason
  and legacy replay 28/28, AI-adoption regression 10/10.
- Linux worker: 530 passed,330 opt-in ImageMagick packaging cases skipped. Metadata
  source/client security and current permission tests passed; all 31 worker Python
  files match the running acceptance image.
- Actual acceptance browser: archive filters/checkboxes, note search, live folder
  navigation, imported metadata, synthetic editor, Settings and mobile width pass;
  zero browser errors/non-auth writes. Migration fingerprints, all 100 source-tree
  listings and synthetic metadata file hashes checked. Test-runner isolation 10/10.

### Resume and remaining boundaries

The ignored acceptance folder contains `PREHLED-VERZE-4.md`, exact test journals,
backups and guarded `start-test.ps1`; it starts the existing loopback instance
without resetting data. Standard isolated test runners remain in `scripts/`.
Do not clear the preserved Windows diagnostic operation manually. Next external
acceptance needs a compatible NAS worker mount and the full production workbook.
The deferred AI-description CSV exchange and live publication/GCS/Notion require
separate scope/configuration. No production deployment or merge is performed.

## Previous checkpoint (2026-09-26, database tables and project import)

Continued the existing `codex/autonomous-pbr-completion` worktree from
`cd698f47c29240ed843cdd56aaa7a3fa40c20316`. Main remains the verified
`88a1f99d748d2a0edbb1fce509e13d18bfc03908`; no removed auth branch was used.

- Materials search includes Note with literal escaping and processor scope intact.
  Material detail opens its preview, preferring FABRIC_1 then SPHERE_1.
- Publication preparation/history now lives in Materials over frozen filtered or
  selected IDs (maximum 100; larger sets require narrower filters). The old URL
  remains compatible but its navigation item is removed. CSV/ZIP retain existing
  source, technical and content approval checks; manual Published is separate.
- Projects/Companies have filters, search, inline edits, visible columns and
  reviewed bulk changes with per-row outcomes, version checks and exact-key
  recovery. Pending/unknown writes block SPA, Back and native leave navigation.
- Projects store copyable NAS folder references. Read-only planning preserves
  folder names, four-digit numbers and paths. Legacy missing specifiers are
  marked; ambiguous numbers/invalid names are quarantined. No inferred Done,
  deadlines or historical material/project assignments.
- Catalog has fixed type tabs, name/code/date/activity filters and editable codes
  and Active. Names and collection ownership remain immutable publication
  identities; explicit replacement creates a separate value, without silently
  moving assignments. See [catalog contract](catalog-database.md) and
  [project/company contract](project-company-tables.md).
- Migrations 0028/0029 preserve old audit/receipt schemas and publication hashes;
  downgrade refuses populated folder references or non-reconstructible history.

### Owned local acceptance environment

The existing loopback R100 instance was backed up before migration. Per-table
fingerprints confirmed all previous rows unchanged (ignoring only added fields),
including 100 material records. `alembic check` found no schema drift. Private
backup: `before-project-catalog-20260925T223620Z.private.dump` in the ignored
acceptance directory. No production/demo database or NAS source was modified.

Read 265 immediate project directories. Imported **251** unambiguous projects,
including **25** legacy names without a material specifier. Quarantined **14**
folders: 10 invalid names, 4 folders sharing duplicate project numbers. Private
plan, journal and exception CSV remain under ignored `tmp/pbr-acceptance-20260925`.
Created 109 manufacturer-labelled test companies, preserving the original company,
and moved 18 existing brands to matching company entries. These labels do not
assert legal corporate relationships; no addresses, tax data or websites invented.
All original material fields and historical null project assignments were preserved.

Added one explicitly synthetic ZIP/CSV material (`#zip-test`) and its own supporting
company/brand/project. Current totals: 101 materials, 252 projects, 111 companies.
The fixture uses generated real PNGs and an actual read-only Linux worker plus
packaging runtime; source/workspace are local owned paths, with no NAS mount.
The worker and packaging service are on an internal network, exposed only through
an owned localhost relay with fixed targets. Source mutation, GCS, Notion and
local-copy retirement stay disabled. Historical previews remain dated local
snapshots. Done/ZIP for real NAS materials still needs a validated live read-only
worker path; the synthetic fixture does not establish real-source readiness.

ZIP exercise passed: one CSV row, one ZIP, three downloaded artifacts, SHA/ZIP CRC
checks, unchanged source bytes, unchanged 100 original material records and
Published=false. No upload was dispatched. Private exact-request journal supports
resume without duplicate records or duplicate packaging execution.

### Validation

- Linux backend: **2,109 passed**, 470 PostgreSQL-gated skipped in this no-DB run.
- Full isolated PostgreSQL: 469 passed, one old-schema fixture failed because it
  selected the new column before its migration. Fixed that fixture; targeted rerun
  **31 passed**, including the former failure, new migrations/concurrency and all
  27 auth checks with zero skips. Thus all 470 distinct PG cases are covered.
- Frontend full suite: **990 passed**; added navigation coverage and related
  regression set: **63 passed**. Lint, TypeScript and production build pass.
- Full E2E: **25 fresh + 25 retained passed**, run
  `c5fab549-9ae8-472b-95b2-60143af51363`, project
  `reawote-e2e-project-catalog-v3`. Owned containers/networks removed; synthetic
  volumes/artifacts retained; protected original/demo state unchanged.
- Read-only browser checks on the actual R100 instance: note tag search, original
  and synthetic material previews, 251 linked projects, 111 companies, 76 catalog
  rows and code filtering passed. All four list pages fit the 390 px viewport;
  no browser errors or non-auth writes. Visual review corrected long-option
  mobile overflow and condensed/styled catalog filters and tabs.
- Final CSS-only adjustments rebuilt successfully. Artifact values remain local
  and private; no customer data or credentials entered tracked documentation.

Build advisory: main JS remains slightly above Vite's 500 kB warning threshold.
No live importer compatibility or multi-gigabyte performance claim is made.

## Latest checkpoint (2026-09-25, editable material table)

Resumed from `9a40b825377307309b79fe438f62a29317241c99` on the existing
`codex/autonomous-pbr-completion` worktree. A fresh remote read confirmed
`origin/main` remains `88a1f99d748d2a0edbb1fce509e13d18bfc03908`.
The deleted auth experiment was not used. The user's request to continue follows
the agreed list editing, bulk changes and revised field/category requirements.
See [the material table contract](material-table.md).

- Added small lazy thumbnails and direct Project, Processor, Status, Checked,
  Published and Note edits. Column visibility, order and width are persistent
  display preferences. Existing list/gallery filters remain shared.
- Bulk review freezes all selected IDs and their revisions, including offscreen
  filtered results. Writes are sequential, individually receipted and audited.
  Unknown outcomes pause and retry the exact key; partial successes are retained.
  Closing/unmounting never starts later jobs. Notes support explicit multiline
  saves and are not discarded when another cell is saved.
- Done retains safe source preflight and metadata snapshots; missing metadata.txt
  remains supported. Checked is human no/OK/Correction, with Correction reopening
  production and a fresh Done transition clearing the previous check. Published
  is manual boolean evidence. Existing technical pipeline histories remain intact.
- Brand/category controls use the controlled identity planner for linked folders.
  Unlinked unpublished materials in progress can use a database-only confirmation
  with numbering reservations and history. No original NAS files were changed.
- Read all 75 rows of TEXTURE-CATEGORIES_2026.xlsx (18 groups, 57 subcategories).
  Backend and frontend vocabularies agree, retain exact codes/order and show full
  paths for repeated names. Migration 0027 adds Checked/Note and seeds online
  categories without guessing assignments or replacing legacy values.
- Updated the exact private R100 acceptance app at port 58363 after a custom-format
  PostgreSQL backup. Fingerprints proved every pre-existing database row unchanged
  by migration, including user changes; all 100 materials remain, with 76 online
  categories (75 supplied plus one existing custom value). Source writes remain
  disabled. Private credentials, dump, preview copies and screenshots stay ignored.
- Read-only R100 UI acceptance verified 100 rows, primary thumbnails, 75 category
  choices, Properties, a 100-row bulk review and no page overflow at 390x844.
  Zero record writes, blocked writes or HTTP/page errors. First visible preview
  took about 3.9 seconds in that run with integration tests running concurrently;
  this is a local snapshot observation, not a live NAS performance guarantee.

Verification:

- Full frontend: **968 passed / 59 files** (46.18s). TypeScript, ESLint and
  production build passed. After the final human-readable confirmation labels,
  all **10 table tests** passed again, followed by lint/types/build. Final main JS
  504.40 kB (147.88 kB gzip), CSS 35.05 kB; Vite reports its 500 kB chunk advisory.
- Full backend in an isolated read-only Linux container with no network/mounts:
  **2098 passed**, **466 PostgreSQL cases skipped**, exit 0. Counts were checked
  against pytest progress and skip summaries (double quiet mode omits its total).
  PostgreSQL is covered separately below; no skipped case is counted as passing.
- Final real PostgreSQL run `reawote-test-1d75f434d8be4367876e3378cf449b1b`:
  **467 passed**, 799.83s; auth gate **27/27, zero skipped**. Includes historical
  upgrades, clean/refused downgrades, exact history/receipt guards, old receipt
  replay after migration, new tracking history and competing stale cell writes.
  Owned containers/network were removed; its isolated volume was retained.
- Final guarded Playwright run `d36dea75-3776-4840-a1c0-b0c07d32a82a`:
  **25 fresh + 25 retained passed**. It exercises bulk Done using synthetic folders,
  Checked OK/Correction/reopen, lost-response exact-key recovery, one Note audit,
  metadata snapshot counts, Published and persistence after the service restart.
  Protected regular/demo resources remained unchanged; only owned containers and
  networks were removed. Successful synthetic screenshots/traces are retained.
- R100 backup `before-material-table-20260925T152932Z.private.dump` is 384,021 bytes;
  `pg_restore --list` verified its archive and material data entry. Migration row
  fingerprints and the read-only 100-row UI acceptance both passed. No test batch
  was applied to the actual R100 materials. The final confirmation-label refinement
  affects presentation only and is covered by the final targeted frontend run.

Earlier attempts and corrections:

- The first PostgreSQL image omitted the vocabulary JSON from its installed wheel;
  added package data and stopped that owned run. The next run had 459 passes and
  seven historical-fixture failures because the current ORM tried to read new
  columns before their migration. Historical setup now uses its actual schema;
  none of the production migration guards was weakened. The final run above passed.
- Earlier browser attempts exposed newly intentional preview aborts on navigation,
  an asynchronous checkbox assertion and ambiguous Status selectors. Assertions now
  ignore only exact cancelled preview GETs and scope facts to the material panel;
  checkbox state waits for the real response. Final fresh/retained suites passed.
- The full Windows backend attempt was stopped because platform/subprocess tests
  need the canonical Linux environment. The full isolated Linux run above passed.


### Remaining acceptance boundary

R100 still uses the dated local preview snapshot. Its live source worker is not
connected, so real Done preflight and linked identity planning cannot complete in
that instance. The table reports the unavailable worker as a known no-write result
and stops the remaining batch. Those transitions are exercised with real synthetic
folders in the isolated E2E environment. No original/demo database, main merge,
production deployment or live external integration is part of this change.

## Previous checkpoint (2026-09-25, REAWOTE visual identity)

Resumed from `72be535fd5735eb378ecd3ba7daf9c5b6c0b111c`. A fresh remote-main
read and the clean original checkout both remain at
`88a1f99d748d2a0edbb1fce509e13d18bfc03908`. The user requested the existing
workspace to follow the supplied REAWOTE logo, Poppins archive, brand guidelines
and the public website. See [the visual contract](brand-interface.md).

- Replaced the placeholder letter mark with the supplied horizontal logo in
  desktop/mobile navigation and authentication. The PNG keeps its aspect ratio.
- Bundled Poppins Light/Regular/Medium/SemiBold/Bold with `font-display: swap`.
  OFL is retained with the source and copied into the deployed assets. No external
  font service or package dependency was added.
- Centralized navy/lavender theme tokens and readable semantic status colors.
  Shared headings, panels, pills, fields, tables, menus, dialogs, gallery and
  empty/error states now use the same identity. Added visible keyboard focus and
  reduced-motion handling; mobile account names truncate within the header.
- Production frontend rebuilt for the existing private R: acceptance app. The
  NAS source files, database schema, backend/worker logic and material data were
  not changed by this presentation task. Only read-only inspection of supplied
  brand assets and ordinary read-only acceptance navigation were performed.

Verification:

- Full frontend: **959 passed / 58 files**, 32.70s. Lint and production build
  passed. Final build after mobile-header refinement: main JS 481.97 kB, CSS
  31.36 kB; five font assets total 716,028 bytes. No test assertions were weakened.
- Computer Use visual checks: actual R: catalog, dashboard, material form,
  table, gallery and next-image control; small gallery and navigation at
  390 × 844. No horizontal page overflow in checked mobile views. Confirmed
  computed Poppins, heading weight 700 and `rgb(31,36,68)`. The final account
  control is 41px high inside a 68px header. Viewport override was reset.
- Initial guarded E2E run `706867f1-8fd3-42fe-b4a2-180c8866798b`:
  **24 fresh passed**, 2.0m; **23 retained passed / 1 failed**, 1.1m.
  `resource-history` failed in Chromium navigation with `net::ERR_NO_BUFFER_SPACE`
  after its API history assertions passed, before rendering the brand page.
  This is recorded as an intermittent browser failure, not a passing test or a
  proven application defect. Gallery, catalog, auth and dashboard passed both
  phases. Owned cleanup completed; diagnostics remain in ignored artifacts.
- Final guarded run `dc832eb4-cccf-4ed4-9162-c57e8d89e7a8`, including the
  mobile-header refinement and deployment font-license copy: **24 fresh + 24
  retained passed**, 2.0m/1.1m. No simultaneous manual browser navigation was
  performed during this rerun. This does not prove a cause for the earlier
  intermittent error. The runner confirmed unchanged protected regular/demo
  resources and removed only its owned containers/networks. Test volumes and
  synthetic visual artifacts were retained.

Final E2E image `.Id` values:

- backend `sha256:13546d9aa9b51eaec40952c9a1cd94c449f0c4b8cce5828060b93ee6a81d9e01`;
- frontend `sha256:6f34da2d49fd4c238974d194aa8453c7a08cfd21d637ac4b9afcf10832bb9861`;
- worker `sha256:7dc3609a62258a5da6465e4f3bedb6cd1894db6d05c04be01002e1228d7a40b2`;
- packaging `sha256:be4a2b0c4da128fec1390d6499cc60b969902f6c6a601b0f56a614b2b1ab341c`.

## Latest checkpoint (2026-09-25, Materials preview grid)

Resumed from `2a5433a60e9abadaab7273c9a3692d43069857ab`. Fresh remote main and
the clean original checkout still equal `88a1f99d748d2a0edbb1fce509e13d18bfc03908`.
The user clarified that visual comparison means a filtered gallery on Materials,
not a separate two-material comparison tool.

- Replaced Compare navigation/page with a List/Gallery switch under the existing
  filters. Four regular tile sizes, small captions, direct detail links and corner
  previous/next PNG controls. FABRIC_1 then SPHERE_1 are preferred; other PNGs use
  natural filename order. Old `/compare` links open the gallery for compatibility.
- Thumbnails request 256/512 pixels; detail retains 1024. The worker decoder and
  backend transport validate the requested bound. Source/hash binding, no-follow
  filesystem access, decode limits, before/after authorization and no-store
  responses are preserved. No migrations or dependencies changed.
- Near-viewport reads, a two-request queue, cancellation, revoked object URLs and
  a session/page-owned 32 MiB/160-image memory cache bound browser work. Entries
  expire after 60 seconds; explicit refresh discards them. Display preferences
  alone persist. See [the loading and freshness contract](preview-gallery.md).
- Private R: acceptance snapshot: 280 PNGs for 95/100 materials, 94 with a preferred
  primary filename, four folders without PNG and one without PREVIEW. All 840
  derived images passed schema/hash checks. No source writes. A private adapter
  serves the dated snapshot through ordinary authenticated preview routes; it is
  not production code and does not establish a live NAS worker connection.

Verification:

- Backend preview suite: **40 passed**, 25.76s; two dependency warnings.
- Actual Linux worker preview suite: **51 passed**, 10.41s, no skips; nonroot,
  read-only, no network, no host mounts. Inspected image `.Id`:
  `sha256:d4b9e98bb13e9137ca057948f98f93f3c32e6f3200089254589b2c3201883a3a`.
- Full frontend: **957 passed**, 31.21s. Two additional transport/cache-budget
  cases were then added; their final overlapping suites passed **30 tests**, 3.02s.
  Lint, production build and E2E TypeScript passed. Main bundle 479.47 kB.
- Initial guarded browser run `338e8177-beaa-4825-8cf3-2091a574eaa4`: **24 fresh
  passed**, 2.0m; retained **23 passed/1 failed**, 1.4m. The failure was Chromium
  `net::ERR_NO_BUFFER_SPACE` on an existing Done-detail reload. The gallery passed
  both phases. No assertion was weakened; a new isolated full rerun follows.
  Desktop/mobile gallery screenshots were inspected, including arrow/caption
  placement and no page overflow. Owned cleanup and protected-state checks passed.
- Snapshot helper's first attempt rejected 272 images because Windows SMB `stat`
  and `fstat` report different ctime semantics. The private helper now compares
  identity/size/mtime across APIs and ctime before/after within each API. The full
  completed read found zero failed files/folders. Linux production checks were
  not relaxed. Private snapshots, workbook values and credentials are excluded
  from git.

- Second guarded run `fc0bd075-dc35-4464-b56a-023eba3f34d4`: **23 fresh passed/1
  failed**, 2.6m, no retained pass. The existing archive scenario timed out while
  the page showed Loading material lifecycle, after its API state/history checks
  succeeded. Gallery passed again. This is recorded separately from the first
  browser buffer error; its cause is unconfirmed. No tests were disabled or
  given automatic retries. The final isolated whole-suite rerun below passed.
- Actual R: snapshot browser acceptance: **95 decoded previews + 5 explicit empty
  states**, four sizes, arrows, filters, cached mode toggle, reload and mobile
  layout passed with zero non-auth writes. Eight initially visible materials:
  **1586ms**, 12 near-viewport listings, 338,641 image bytes. All 100 cards scrolled
  in **8669ms**; cached filtered List/Gallery toggle **123ms**. These local snapshot
  timings do not measure live NAS performance. Private desktop/mobile screenshots
  were inspected. The first private browser checker incorrectly counted the
  expected unauthenticated session 401 as a failure; after allowing that specific
  pre-login challenge, the unchanged application passed the complete checker.

- Final guarded run `b3e50b9a-6805-473a-9738-efb14bfffd42`: **24 fresh + 24 retained
  passed**, 2.0m/1.1m, with no concurrent NAS snapshot preparation or other browser
  test. Existing Done/archive checks and the gallery all passed without assertion
  changes. This does not establish a cause for the two earlier intermittent
  failures. Protected regular/demo resources were unchanged; only owned containers
  and networks were removed, and owned volumes/artifacts were preserved.

Final E2E image `.Id` values:

- backend `sha256:24a8aee33ed40e62b1b0f0a9948458466960ed83056a1390f3a90a3d4a34680e`;
- frontend `sha256:e71261f1f8211501ad68b95664e4fb0429d87d0df2a3e5272c60f39eddace7bc`;
- worker `sha256:ddc015271916de3b666e8327e0e490a645367522a84b986c2c32db8b22f2ba51`;
- packaging `sha256:8fba62ceae73c66f84b0b0eb37d696e507e87c07598d8c3b412ad240553b5af3`.

The existing PostgreSQL/full packaging baseline remains in the previous checkpoint;
those unrelated broad suites are not claimed as rerun. All validation processes
have finished; only the private 100-material app and its PostgreSQL are intentionally
left running. No merge/deployment, source write or original database change occurred.

## Previous checkpoint (2026-09-25, historical R: sample)

Resumed from `44fcb2673fd606290ec660536e06006c894bb2a1`; freshly read remote main
still equals `88a1f99d748d2a0edbb1fce509e13d18bfc03908`. Original checkout remains
clean. The user's latest direction narrows source operations: catalog references
must preserve the existing tree, historical materials have no project, and any
future material rename/new directory workflow must follow the manufacturer rules.
See [the actual 100-row acceptance and remaining limits](historical-r100-acceptance.md).

Implemented:

- Four-part named identities across new record creation, historical import,
  technical image proofs, packaging map names and controlled identity planning.
  Exact historical spelling is retained; persisted three-part records remain
  readable. Missing metadata remains nonblocking for catalog import.
- Optional historical project and explicit relative folder references. No source
  IO occurs during import. Catalog overlaps and active source ownership block a
  batch; confirmation reacquires ownership before writing. New migration 0026
  makes project nullable and refuses downgrade with unassigned records. Migrations
  0001–0025 are unchanged.
- Nullable project rendering, later assignment, optional import mappings and
  folder paths directly in the material list. Imports page is loaded separately.
- A private 100-row workbook and actual isolated API/browser acceptance. The app
  and its PostgreSQL remain available locally for manual testing; source writes,
  packaging and external integrations are disabled. No agent runs in the service.

Verification for this increment:

- Broad Windows backend run: **2066 passed**, 1701.82s; **14 setup errors** were
  exclusively access denied to the default Windows `pytest-of-Admin` temp root.
  Every one of those exact 14 cases passed in a fresh owned worktree temp root
  (**14 passed**, 12.76s). No test assertion failed and none was skipped. This
  broad process collected before the final proof/collision cases were added;
  the separately listed final 39/4 runs cover those changes. Do not describe the
  first invocation as a clean full run or collapse overlapping runs into a total.
- Relevant backend/import/naming suite: **411 passed**, 170.28s. Final identity
  protocol regression after the E2E-discovered base-name mismatch: **39 passed**,
  61.00s. Late folder collision/active-owner tests: **4 passed**, 10.31s.
- Full frontend: **951 passed**, 39.02s; lint/build, E2E TypeScript and direct
  invocation guard passed. Main chunk 474.01 kB; no raised warning threshold.
- Actual PostgreSQL: **464 passed**, 838.31s, auth **27/27**, no skips. Project
  `reawote-test-45e8196aab7d4c3ab058c0cc1080f1ed`; owned containers/network cleaned,
  database volume retained. An earlier run had 463 passed and one obsolete
  three-part 9999 assertion, corrected before the full green rerun.
- Full Linux worker/packaging image: **822 passed**, 586.23s, no skips. Image `.Id`
  `sha256:460cc39264c882882d37f59023afc986d297b3b998791e06c1a11d290375a17b`.
  The earlier thin-image run's 330 skipped packaging tests are not claimed as
  acceptance; the full dependency image supplied the final result.
- Guarded browser run `56a28942-2b11-4d69-8664-a94686fb8efd`: **24 fresh + 24
  retained passed**, 2.2m/1.3m. Protected regular/demo resources unchanged; owned
  containers/networks removed and volumes retained. The first browser run caught
  backend proof validation missing the worker's base-name rename support. Fixed
  strictly, with forged-prefix negative tests, then reran the complete browser suite.
- Actual private R: subset: **100 records/100 exact paths/100 NULL projects**,
  identical import replay, all API details verified after PostgreSQL restart and
  actual UI search/detail/reload/mobile checks. No real NAS gallery/texture test
  is claimed because Docker cannot currently mount that network drive.

Inspected final E2E image `.Id` values:

- backend `sha256:c48786de9a9b4c285e5127b3428d6934fbe39b42cea25151fd13cee32020e4c2`;
- frontend `sha256:f4d3cb15b8d3afd36f0ea73fff5de2d7231cdb8e82f9d4d125d2612b234c96b3`;
- worker `sha256:d09e268786889c69dc2572e538f04eea3a80bdefd319f42f6c0ce755836e1674`;
- packaging `sha256:fd5ffa4da6824ffb70dad4e6b38b3d3dc02b6dc85080ac22aa89c14b90eda1e2`.

Implementation checkpoint: `e590ada`. Private source values and credentials are excluded from git. No main merge,
deployment, original database migration, NAS write or real cloud operation occurred.
All validation processes have finished. Only the owned local 100-material app and
its database remain intentionally running for manual testing. The private access
file, short Czech checklist and ownership-checked restart script are next to the
sample workbook in the ignored acceptance folder. Documentation checkpoint
`e4872f4` was pushed with the implementation; this final verification note follows it.

## Previous checkpoint (2026-09-25, production dashboard)

Resumed the owned `codex/autonomous-pbr-completion` worktree from clean
`8d86c72dc3beb12c472416a3746c4694b5bf2b9a`. Remote `origin/main` was read again and
remains `88a1f99d748d2a0edbb1fce509e13d18bfc03908`. The original checkout is clean.
The removed experimental auth branch was not inspected or used.

The remaining home-page placeholder is now a [production dashboard](production-dashboard.md):
one authorized material-list snapshot, recorded status counts, filters/search,
ten results per page, manual refresh and ordinary workflow links. Processor scope
comes from the existing server endpoint. Account/role changes discard prior data
and late responses; failed refreshes hide stale counts. DONE is explicitly separate
from approval/publication. No new backend API, dependency, migration or external IO
was introduced. Existing migrations 0001–0025 are unchanged. Browser pagination
does not solve the existing full-list endpoint's unverified production scaling.

Verification for this increment:

- Before edits: **50 relevant existing frontend tests passed**, 8.37s.
- Initial focused Dashboard/App/regression run: **36 passed**, 5.26s.
- Initial lint/build found an unused predicate parameter and a possibly undefined
  list in a callback; both were corrected without weakening checks.
- Full frontend suite: **949 passed**, 33.37s, no skips. The initial build then
  warned about a 502.68 kB main bundle; the dashboard is now loaded separately.
- Final affected Dashboard/App/auth/regression suite: **53 passed**, 5.30s.
  Lint, production build and E2E TypeScript check passed. Main chunk is 498.56 kB,
  dashboard chunk 4.46 kB; no bundle-size warning or raised threshold.
- Real browser run `ae824fd0-546e-405c-aa80-75b66baec976`: **24 fresh passed**, 1.8m,
  and **24 retained passed**, 1.0m, no skips. Dashboard assertions compare real
  API records, processor assignments and counts, navigate to actual details,
  and recover an intentionally aborted read without any dashboard write.
- Four dashboard screenshots (fresh/retained, desktop/390px) were inspected;
  no horizontal overflow. Artifacts remain under the owned run's
  `.e2e-artifacts/.../playwright-results/{fresh,retained}` directories.
- Direct Playwright invocation safety check passed with zero POSTs/fixture writes.
  The runner preserved protected regular/demo state and removed only its own
  containers/network/run fixtures; owned database/identity/packaging volumes remain.

Inspected E2E image `.Id` values, in component order:

- backend `sha256:61d158e7a15efb11a03c8a4d0a48247476507c44ac1caaba065df6b73d53a411`;
- frontend `sha256:9e4811c2adfa2a60e38e9a8a26db84985ce9e60195fb6b019e6a702e78093b6a`;
- worker `sha256:f9ed13388c0d2b58dc727a46cfb1e2d623a118fab2ef13ca4422b720ba048ee9`;
- packaging `sha256:e97940d101ef8dde218c5e9e796c38302f46ac5dce0f61e9b3201d4935670ec7`.

The standalone PostgreSQL/concurrency and complete Linux worker suites below were
not rerun for this frontend-only change; their dates and scopes remain explicit.
The Docker browser run used its actual newly built backend/worker/packaging images.
No test process remains running. No merge, production enablement or external write
occurred. GitHub CLI and a direct GitHub connector remain unavailable; no draft PR
was created. The remote branch and review handoff remain the review artifacts.

The actual importer/golden PBR reference, representative historical inputs, live
integration targets and deployment/backup decisions remain missing. The next
acceptance task is still one real reference CSV/manifest/ZIP compared with the
intended importer. Dashboard work does not remove these blockers.

## Previous checkpoint (2026-09-19, verified PBR review candidate)

Owned worktree: `C:\Database\Database\tmp\autonomous-pbr-completion`.
Branch: `codex/autonomous-pbr-completion`. Implementation checkpoints:
`1ed3b44339b42558513ebdf19ddb32b8e52545e1` (authorized recoverable retirement API)
and `776a36a4a8d3aef4f7f4a8a8eb4464bfd657972e` (reviewed UI and actual E2E).
API/migration 0024: `249b9e6e3a88495544b8eaaaf44c5ba50241972c`; Docker source
mapping guard: `c3a0d1631f06e372284409da5fe003b472cd0b45`. Remote main last
verified unchanged at `88a1f99d748d2a0edbb1fce509e13d18bfc03908` during that push.
Shared controller extraction is committed as `9957a0ef8729f23175412ac2f295886c35d76bc2`.
Account profiles (`6c83a33`), temporary error cleanup (`7565a1c`), incomplete retained
copy cleanup (`e5679b6`) and reconciled docs have been pushed. Remote main remains
unchanged at the stated base.

The [consolidated review handoff](pbr-review-candidate.md) records implemented
behavior, safe reproduction, migration/rollback boundaries and concrete missing
external inputs. All six baseline migrations compare unchanged against main.
There are no running test sessions at this checkpoint. No merge, production
enablement or external write was performed. Remaining acceptance work below needs
real fixtures/targets or a separately authorized deployment/integration step.

### Application retirement API and operator controls

The [application API](packaging-retirement-api.md) implements ADMIN/CSRF/proof-bound
intent and recovery with dedicated execution leases and short transactions.
Committed intent quarantines new downloads/staging; existing worker-locked
readers retain account rechecks. Exact replay performs no IO. Factual receipts
survive actor revocation or lease loss, and another administrator can recover.
The separate backend flag defaults false. No production enablement occurred.

Relevant expanded API verification passed **117 tests with three test-adapter
failures**, 299.85s. All 24 retirement cases passed; the existing download failure
spy needed to forward the new `opening` keyword. After correcting that adapter,
all **32 targeted download/config cases passed**, 10.79s. The first focused run
was **20 passed / 1 failed**, because the disabled-feature fixture tried to mutate
frozen Settings; it now constructs the app with a copied Settings value.
The final focused retirement/download suite passed **48 tests**, no skips, two
dependency warnings, including an already opened reader finishing while new
readers are quarantined and the synthetic worker reports BUSY to removal.

The first complete application PostgreSQL run was **462 passed / 1 failed**,
674.89s, **auth 27/27**, no skips, four dependency warnings. Run
`reawote-test-95e57e08988a40949299c5d2b7d5d448`, image
`sha256:470a09f99860da0b5f2fd53abee158a336860138921a5c22d4e5884c96e5eeb8`.
The new real account-demotion test expected 403, but the established account
endpoint revokes sessions on role changes and correctly returns 401. Only that
expectation/comment changed; guards remain intact. Full corrected PG run
`reawote-test-e58187a18ac2461bbe69ff3d8c40e977` passed **463 tests**, 661.61s,
**auth 27/27**, no skips, five dependency warnings. Owned containers/network were
removed; test image/volume retained. Inspected image:
`sha256:1674244796e07dd1a7e5c1c125182daaf6426f4f0fc5745813fc2eb06e5619cf`.
Runner offline gates: **10 passed**, no skips.

UI/client work is implemented: read availability before download controls,
explicit ADMIN confirmation, exact unknown-response packet, read-only recovery,
actor/proof-bound late-callback isolation, monotonic REMOVED display and history.
The frontend suite passed **935 tests**, 27.19s; lint and E2E TypeScript passed.
After actor/time display, wrapping and lazy loading the copy controls, all **64
affected client/component tests passed**, 3.72s; lint/E2E TypeScript passed again.
The final build passed in 2.79s. Loading the copy controls only when needed moved
11.36 kB into its own chunk and reduced the main bundle to 498.37 kB, eliminating
the earlier 508 kB bundle warning without raising the warning threshold.
The first browser run `f32051b5-4e0a-4eae-aa09-afa36b7c75f3` passed **22/23 fresh
scenarios**; the new scenario searched compact packaging summaries for a reason
that is only in job detail. The test now reads the real details before selecting
its two accepted copies; application contracts remain unchanged. Retained tests
did not run after that failure. Final run `d2817df1-6b1b-4262-a6b2-9eb6f9c682af`
passed **23 fresh scenarios**, 1.7m, and **23 retained scenarios**, 58.6s, no skips.
This uses the actual browser/backend/PostgreSQL/private packaging service. It
creates two real accepted copies, rejects removal while staging owns one, retains
the original download across restart, removes only the second copy, deliberately
loses its committed response and recovers by GET without another POST. Removed
downloads remain blocked after restart; PACKAGED proof, CSV and closed staging
history remain unchanged. Four fresh and two retained desktop/mobile screenshots
were visually inspected; 390px pages have no horizontal overflow.

Inspected E2E images:

- backend `sha256:04bd625f6776db51249fc095e88af20a59350e255da079753586267477023731`;
- frontend `sha256:79f5e1e5afacb1a51c3075b2c2b9569eec13083403df6c5e943789453afde6ee`;
- worker `sha256:00bf377a370e03245e0d230d013e91feb7ab5ebca6d9f10f02f56cb4ab3fd112`;
- packaging `sha256:1d191b5d32de0472656d37ec0e468ce49b5187de83123392b0dc0bdd1d6cf11b`.

The runner removed only its containers/network/run fixtures, retained its database
and identity/packaging volumes, and confirmed protected regular/demo state unchanged.
All E2E helper safety cases passed; direct invocation failed closed with zero POSTs/fixture writes.
The browser's fresh/retained screenshot directories are now separate, preserving
review evidence from both passes. No real cloud access is enabled.

### Retirement database evidence (0025)

The additive [retirement schema](packaging-retirement-database.md) now preserves
intent, ordered dispatch and factual observation as append-only evidence, while
keeping existing PACKAGED history unchanged. Matching references, accepted-proof/
manifest checks and material-row locking exclude active/new staging claims for the
same copy. Verified receipts bind every intent field; late uncertain facts cannot
erase a receipt. Populated retirement evidence refuses downgrade.

The complete isolated PostgreSQL phase **passed 459 tests**, 641.03s, **auth 27/27**,
no skips, four existing dependency warnings. Run
`reawote-test-2e40bc8235254f23a219e729ecd0d40b`, immutable backend image
`sha256:fa541a68885aefb1eaead568a90344f544b35a2dc1775f6563a189d8b0f4438e`.
This includes fresh/prior upgrade, Alembic current/heads/check, strict receipt and
history guards, empty/populated downgrade and real competing retirement/staging
claims. Owned containers/network were removed; owned test image/volume retained.
Relevant local schema/packaging backend tests **134 passed**, 213.74s, no skips;
the runner's **10 offline isolation cases passed**.

That schema checkpoint and immutable image predate the separately verified
application route/config/download integration described above.

### Private retirement boundary

The separately opt-in private retirement endpoint holds the recorded execution
lease and binds its READY proof/roots before removal. It preserves execution
history and supports exact receipt recovery with NAS offline. Delayed ordinary
dispatches inspect retirement state before writing any command, so a late CLOSE
cannot strand receipt recovery in CLOSING. New downloads report a fixed retired
code. No application user action, database migration or UI is enabled yet.

The independent backend client validates every receipt binding and file/byte
total against the accepted request/report/result; responses are at most 4096 bytes
and 150 seconds, without automatic retry. Final relevant backend client tests:
**164 passed**, 3.60s, no skips, two existing dependency deprecations. They include
independent validation of an actual HTTP/restart/lost-receipt synthetic export.

Before the additional delayed-dispatch fence, the affected Linux suites passed
**201 tests**, 280.10s, no skips, two existing warnings. Owned run
`reawote-retirement-api-947f1213c9f54d89834371d5d7c97919`, image
`sha256:e402ae3ec89cbb2a4ce3f8573efcdd4640185c696e2606437404410cefd7c8b7`.
Actual HTTP/conversion/closure/offline retirement/lost-receipt/restart smoke and
synthetic contract export passed in owned
`reawote-retirement-smoke-c6ef9d25a3af49faa4aa1d3e259bd154:runtime`, image
`sha256:0ab0c5c2a0db237cc00af29b411aec3bad2d62c5d53b7a09eeae67a3d626cdb3`.
Final affected Linux suites, including the dispatch fence: **209 passed**, 295.96s,
no skips, two existing warnings. Run
`reawote-retirement-fence-7e312e1d687b4db98deafc8459948b54`, image
`sha256:10503f303f88fe0806b797c98f0a5d2b6decf386530cf956770e09934f95302d`.
The final production HTTP/conversion/download/offline/closure/retirement smoke also
passed, including a new delayed reconciliation that leaves execution history
unchanged. Run `reawote-packaging-service-cf0be92a7fef4fd3b210fc6ca321e863:runtime`,
image `sha256:e184c1f145361c655e89318690a4d52d0968e87b7da8468136d51a359d153fab`.
All owned test containers were removed and images retained. This checkpoint
accompanies the private-boundary commit; read the branch tip for its exact hash.
The private-boundary image predates the separately verified 0025 schema above.

### Accepted-copy storage retirement

The internal [retirement primitive](packaging-retirement.md) now verifies an exact
READY result and persists a version-2 REMOVING intent before byte removal. It keeps
the entire original proof/attempt record, binds root/operation and file identities,
supports exact interrupted replay and leaves a permanent REMOVED receipt. Legacy
storage reads/recovery/retention reject both retirement states. No HTTP endpoint,
application authorization/command, database migration or UI is added in this slice.

The complete required-runtime Linux suite **passed 780 tests**, 491.86s, no skips
and two existing dependency deprecations. Owned run
`reawote-packaging-1eaeac8c0c5346c88da20f0ac6792bc6`, immutable image
`sha256:2e1f3ede6fc621687dd9329d8554d40ce2505efc54269c69a4e324eb2620f1f6`.
This final complete run also includes both cleanup slices and their corrected
test fixtures documented below. The owned container was removed; image retained.
New scenarios verify exact recovery after actual process deaths at six removal/
commit boundaries, active-reader exclusion, corruption/replacement refusal,
strict journal validation and unchanged synthetic source files.
Next integration is specified in [the application plan](packaging-retirement-integration-plan.md).

### Incomplete retention cleanup

The worker now verifies and removes only proven incomplete retained incoming copies
after a handled retention failure or explicit reconciliation. It holds the retention
lock, matches the execution-bound artifact root identity and rechecks operation,
root and lock identity during removal. Complete/READY output is preserved/recovered;
unknown, corrupt or ambiguously owned data refuse cleanup. The old journal/proof
remains unchanged until a separately authorized regenerated attempt records history.
Verification/removal uses a maximum 120-second additional cleanup budget.

New cases cover partial and complete copies, fixed request/plan/root bindings, lock
contention, replacement attacks, expired budgets and real process death during file
removal. The complete required-runtime Linux suite finished **740 passed / 1 failed**,
424.01s, no skips, two existing dependency warnings. Owned run
`reawote-packaging-650806ebf0e043f7aaf914e9c939bc54`, immutable image
`sha256:cfdef294a673f215932931e4806de448b7d358d1f0c401efea45646b556e2627`.
The new execution test's unknown-file fixture used default public permissions and
therefore hit the earlier UNSAFE guard instead of UNEXPECTED_FILE. It now creates
that sentinel with 0600 permissions, preserving the exact expected rejection and
all cleanup/source/history assertions. Application code is unchanged after that run.
All affected cleanup/store/execution/dispatch/stage tests then **181 passed**,
228.08s, no skips, in `reawote-retention-62ee3e055ce040aeac4839293f9075cd`, image
`sha256:9a1861c9367355f4e5560ef1d34f71fe62fa7034f4d7b14e856ba214d27850c9`.

The updated production-runtime HTTP/conversion/restart/proof-bound download/offline
replay/ordered closure smoke passed in owned
`reawote-packaging-service-42b736787ee3416ebde89190e20db264:runtime`.
Owned containers were removed; images retained. No database migration, HTTP endpoint,
production or external write is added. This checkpoint accompanies that verified
cleanup commit; read the current branch tip for its hash.

### Packaging failure cleanup

Handled staging/conversion/retention errors now attempt guarded removal of the
durably owned attempt workspace under the existing execution lease. Unknown files,
changed roots and unrecorded ownership still refuse cleanup. Source/master/PREVIEW
and retained output are outside this removal. The durable WORKING/RETAINED journal
is preserved until explicit reconciliation determines whether output committed;
an error does not invent a retryable or completed result.

New Linux fault cases cover immediate cleanup, exhausted byte limits, committed
retention followed by a lost return/journal error, exact ordered replay and preserved
ambiguous workspaces. Verification:

- Complete Linux required-runtime suite: **717 passed / 2 failed**, 387.31s,
  no skips, two existing dependency warnings. Owned run
  `reawote-packaging-a75c916c29ed42a094772f0bb05165de`, image
  `sha256:1e42ac69b667506b9b3178660024bd44586f9a44872bb4ac1d32d87d722ceae7`.
  Both failures were new test expectations for injected OS errors: the existing
  assembly context maps them to `PACKAGING_ASSEMBLY_FAILED`, not the generic
  execution code. Corrected the exact expected code; application code unchanged.
- All affected execution/dispatch tests then **81 passed**, 112.23s, no skips.
  Owned run `reawote-cleanup-d06a432472eb48a89569045fdaab264e`, image
  `sha256:4c11c1b203dcd3c6fae0f4fb84455e446e395a6173383f6e0f0a25d3d3552600`.
- Actual production-runtime HTTP/conversion/restart/proof-bound download/offline
  replay and ordered closure smoke passed, owned runtime
  `reawote-packaging-service-0c32b84a259947f5bfa1af58ac7445ae:runtime`.
- All **11 offline runner-isolation checks passed**. Test containers removed by
  ownership-checked runners; images retained. No database, migration or frontend
  change in this slice. Tests used synthetic inputs and no network at runtime.

Earlier packaging/GCS docs have also been reconciled with the implemented job,
service, download, staging and credential layers. Live verification and retained
artifact cleanup remain explicitly separate gaps.

### Newly completed

Account-profile creation and role/active updates now use the same ordinary
command controller and in-memory registry as other record forms. Pending state
is bound to kind/action/target, not only the shared page URL. It survives navigation,
blocks another ordinary write for the same actor, and offers exact retry or GET
recovery. Current account data are refreshed after success; later administrator
edits are preserved. Account changes retire initial reads and late callbacks.
The actual administration route is `/settings/users`; USER receipt navigation was
corrected to it before adopting the previously unused USER client path.

No backend or migration change. Password issuance/reset stays in its separate
security workflow and no secret values enter these packets. Self-demotion/disable
and the PROCESSOR creation default remain in place.

Verification:

- Shared-form extraction: **52 focused passed**, 7.15s. Initial lint rejected
  reading a ref during render and mutating a memo object used as state. The
  controller now uses immutable lifetime symbols and effect/event-only refs;
  lint passed with the rules intact.
- New account scenarios + existing account/form/receipt cases: **26 passed**, 3.44s.
  Includes unknown creates, target-bound role retry after a later edit, in-app
  navigation, cross-form blocking, actor changes, late completion/reads and no
  dispatch after unmount during digest preparation.
- Full frontend: **891 passed**, 24.82s; lint, build (2.07s) and E2E TypeScript
  passed. The browser suite passed **23 fresh + 23 retained**, 1.6m/54.9s, run
  `9335edd9-3c53-4efb-bdd6-f58157a2192f`. It includes real lost responses after
  profile create and role/status update, explicit GET recovery, exact replay,
  in-app navigation and preserved later administrator edits after restart.
  Four create/update desktop/mobile screenshots were inspected; 390px layouts
  have no horizontal overflow. Owned cleanup and protected-resource checks passed.
- Initial browser run `3f2e0911-2c37-4f8c-b7ca-c18a5a0b6a19`: **22 passed / 1
  failed**, 2.4m; retained pass did not run. Profile creation and GET recovery
  succeeded, but the new test's exact label lookup did not locate the nested role
  select. The snapshot showed the correctly named enabled combobox. The test now
  selects that exact accessible combobox; role/status assertions are unchanged.
  E2E TypeScript and the fresh/retained run above then passed.

Last pushed ordinary-command evidence: PostgreSQL **418 passed** (auth 27/27),
Linux affected regression **346 passed**, prior frontend **884 passed**, actual
browser **22 fresh + 22 retained**. Exact run/image identities, earlier failures,
corrections and visual evidence remain in [historical checkpoints](autonomous-pbr-history.md).
Migrations through **0025 are immutable**.

## Scope and implementation status

The adopted autonomous request authorizes this isolated branch, additive
migrations, synthetic tests and own-branch pushes. Main, deleted `feature/auth-ui`,
original checkout/work, NAS writes, backups/restore resources and production
databases are excluded. The current user separately authorized reading the R:
copy and importing catalog references into a new isolated test DB. No merge/deploy
or real external write has been performed.

Implemented and tested within documented contracts:

- Local Docker isolation, endpoint gates and initial material consistency fixes.
- Session authentication, CSRF, current-role/material authorization, forced password
  change, account administration/recovery and new authentication UI.
- Source inventory, technical reports, technical/content approval, audited reopening
  and invalidation, controlled identity changes and filesystem recovery journals.
- Catalog/content editing, historical CSV/XLSX import, bounded source discovery,
  material preview grid/detail gallery, AI proposal provenance/adoption and scoped services.
- Publication preflight, immutable export batches and exact CSV; packaging planning,
  conversion/ZIP execution, durable reservations/dispatch/lease/recovery, accepted
  proof and authorized historical downloads.
- Configurable GCS staging transport, bounded streams, immutable staging jobs,
  explicit start/recovery/abandon UI and opt-in renewable ADC credentials (`0ff994c`).
- Disabled-by-default Notion comparison and reviewed selective local adoption,
  request-bound recovery, immutable company history and UI.
- Resource/account audit with immutable PostgreSQL ledgers and ADMIN paged UI;
  older material/catalog histories now have cursor continuation as well.
- ADMIN material archive/restore (`e9964c5`, `221c9c0`) with preserved identities,
  immutable evidence, current-work exclusion and uncertain-result recovery. Any
  prior external staging dispatch blocks lifecycle changes pending reconciliation.
- Atomic ordinary-write receipts and lost-response recovery in the shared record
  and account-profile forms; current-role/target checks apply to replay and read
  recovery. Account credentials keep their separate security contract.
- Explicit proof-bound local-copy retirement, immutable schema 0025 evidence,
  ADMIN controls, download/staging exclusion, restart recovery and retained receipts.
  Removal stays disabled by default; it never marks a material published.

These are bounded implementations, not a claim of production-ready completion.
The README links the individual feature/operations contracts.

## Next work and real blockers

1. Finish the [real R: acceptance](historical-r100-acceptance.md): read-only NAS
   worker access, live preview/texture checks and explicit mapping of remaining
   Excel properties. The first 100 catalog records already exist. Implement the
   new name/folder and manufacturer-directory rules before enabling source writes.
2. Verify actual importer contract, golden material outputs and manual publication
   confirmation. The later complete production workbook and importer fixtures
   are unavailable; do not invent live verification.
3. Verify isolated live GCS/Notion/AI only after separately authorized targets and
  access are supplied. Credential contract tests do not substitute for live checks.
4. Review production topology, representative workload and backup custody before
   any separately approved deployment. Multi-gigabyte throughput remains unverified.

3D/HDRI remain later scope. Backups are unchanged; off-machine custody is unconfirmed.
Draft PR has not been created: no available GitHub CLI/PR connector was found.
The owned remote branch is the review artifact in the meantime.

## Resume and verify safely

Read this checkpoint, inspect branch/HEAD/status and compare remote main with the
base. Preserve unrelated work. Never use the deleted experimental branch.

- PostgreSQL: `./scripts/test.ps1 -PostgresqlOnly` with local Docker CLI on PATH.
  It owns a new GUID namespace and requires all auth tests; never substitute
  original/demo databases. It removes containers/network and retains own image/volume.
- Linux: immutable owned image, nonroot/read-only/no network or host mounts;
  use relevant suites and record image identity. Worker changes require actual
  Linux worker-boundary tests and the isolated packaging runtime where applicable.
- Frontend from `frontend`: `npm test`, `npm run lint`, `npm run build`, local
  `node_modules/.bin/tsc --project tsconfig.e2e.json --noEmit`.
- Browser: only `./scripts/test-demo-e2e.ps1` with
  `E2E_PROJECT_NAME=reawote-e2e-auto-01a0a64d`, `E2E_KEEP_SUCCESS_ARTIFACTS=1`.
  Do not overlap that namespace or change protected resources during its snapshot.
  Collect fresh and retained passes; inspect changed desktop/mobile UI.

Commit explicit owned files after checks, then push only
`HEAD:refs/heads/codex/autonomous-pbr-completion` to the verified repository remote.
Continue the next unblocked slice; one completed feature is not completion of the task.
