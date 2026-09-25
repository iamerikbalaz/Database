# Historical materials: first 100-row acceptance

## Scope and source policy

The user's 2026-09-25 direction is authoritative: the database catalogs material
records and references to existing NAS folders. Preserve the historical directory
structure and exact names. Historical records have no project; assigning a project
later must be supported. Most records have no metadata.txt. A future explicit
material rename must coordinate the database and folder rename; new physical
materials must be created inside the manufacturer's directory.

This test uses the explicitly selected **R:** copy, never the earlier Y: root for
test writes. No source writes are enabled. Importing references only changes a
fresh, isolated PostgreSQL database. The original/demo databases, their volumes,
backups and main checkout remain outside the acceptance environment.

## Selection and import

- Read the provided workbook's 553 data rows without modifying it.
- Independently checked candidate relative folders on R: **458 exact matches**.
  This is a targeted candidate check, not proof of global folder-name uniqueness.
- Excluded ambiguous identity/brand/category mappings, repeated brand numbers,
  duplicate folder identities and missing required name/processor labels. This
  left **384 eligible rows**.
- Deterministically selected **100 rows, 18 manufacturers and 7 categories**, by
  round-robin over manufacturer/category groups. This maximizes variety; it is
  not a random or proportional estimate of the whole library.
- The private subset workbook retains all 15 original columns and adds original
  Excel row, exact relative folder path and full R: path. The repository XLSX and
  CSV readers accepted the same 100 × 18 literal values.
- Imported exactly 100 records through the actual inspect/preview/confirm API.
  All original folder identities and relative references are preserved, all
  project IDs are NULL. No metadata or approval/publication status was invented.
- Repeating the identical confirmation returned the same batch and created no
  duplicate. All 100 API details were verified again after PostgreSQL restart.
- The actual browser displayed 100 records, 100 folder paths and 100 unassigned
  projects. Search, detail and page reload passed; at 390 px horizontal scrolling
  remains inside the table, without page overflow. The browser acceptance blocks
  all non-authentication writes and any request outside its exact loopback origin.

Private workbooks, source row values, reports, screenshots, connection details and
credentials stay under the worktree's ignored `tmp/pbr-acceptance-20260925` folder.
They are not fixtures, repository attachments or published artifacts. The test
login is in a local private access file, not in this document or command logs.

## Important limits

The test catalog uses one explicitly synthetic company and synthetic brand IDs
and processor accounts. These are isolated reference records, not reconciled
production legal-company/account relationships.

The imported fields currently cover identity, name, category, manufacturer,
processor and folder reference. **Color, sample-size, collection, notes and the
historical done/checked/uploaded columns are retained in Excel but are not yet
mapped to material content.** No historical status implies current approval.

Windows can read R:, but Docker Desktop rejected the network drive bind mount.
The acceptance app therefore still has no live NAS worker connection. For the
Materials grid, a private one-shot Windows helper read only the selected folders'
direct PREVIEW PNGs and generated a **dated local snapshot**: **280 PNGs from 95
materials**, including a preferred FABRIC_1/SPHERE_1 image for 94 materials. One
material falls back to its other PNG; four folders contain no PNG and one has no
PREVIEW directory. No image decode failed in the completed snapshot. All **840
derived JPEGs** (256/512/1024) passed the backend image contract and digest checks.

The private acceptance-only adapter serves those copies through the normal
authenticated material preview routes and cannot open arbitrary NAS paths. The
page banner records the snapshot time. **Refresh previews** refreshes browser
memory; it does not rebuild this private snapshot. New NAS changes require an
explicit rerun of the private helper and restart of this owned acceptance app.
Production code continues to use the secure Linux worker and contains neither
this adapter nor copied source values/images. No originals, folders or timestamps
were intentionally written; ordinary filesystem access-time behavior is OS-owned.

Live NAS preview throughput, source inventory and texture verification against
these 100 folders remain pending. Local snapshot performance and synthetic Linux
worker tests do not prove a live NAS connection.

Read-only browser acceptance rendered all **95 available primary previews** and
the five honest empty states. It checked all four sizes, next/previous arrows,
preserved filters between List/Gallery, preference after reload and mobile layout
at 390 px. At 1440 × 1000, the first eight visible materials loaded in **1.586s**;
only 12 near-viewport listings were requested initially (not all 100), with 338,641
image bytes. Scrolling through all 100 cards took **8.669s**; a cached filtered
List/Gallery switch took **0.123s**. The run transferred 2,635,636 image bytes across
100 image responses including alternate/size checks. These are one-run local
snapshot measurements, not production NAS benchmarks. No non-authentication
writes were permitted. Desktop/mobile screenshots were visually reviewed locally.

Physical creation inside manufacturer folders and a material-name edit that
performs a coordinated folder rename remain follow-up work. The existing
controlled identity workflow can rename/rebrand synthetic fixtures; an ordinary
display-name edit currently does not rename the folder. Source mutation must
remain disabled in this real-data acceptance until the user's narrower workflow
and failure/recovery behavior have been implemented and reviewed.

## Next small increments

1. Give an isolated worker read-only access to the R: copy and verify live preview
   refresh, inventory and missing-metadata behavior on the selected 100 materials.
2. Review/import the remaining Excel properties with explicit mappings and
   immutable provenance, preserving unknown and conflicting values for review.
3. Implement explicit name-and-folder rename and manufacturer-directory creation
   with collision checks, preview, recovery and synthetic filesystem tests before
   enabling any write on the R: test copy.
4. Reconcile the later complete workbook against NAS, resolve duplicate numbers,
   identities and missing folders, then prepare a separately reviewed real import.

No merge, deployment, publication, live cloud write or production import is part
of this result. The separate app can remain available locally for manual catalog
testing; idle local services do not run model agents.
