# Internal staging batch plan and current-input preview

`gcs_batch.compile_staging_plan` independently rerenders the exact CSV and verifies
each worker request, report and complete retained proof. Every selected row needs
exactly one package with the matching identity. The resulting bounded plan includes
all retained files, the CSV, immutable batch/material/package references and the
configured destination. Its hash changes when provenance changes even if file bytes
remain identical. The compiler never reads or writes the database or cloud.

The conservative `INTERNAL_STAGING_V1` layout uses `publication.csv` and
`materials/<material-uuid>/<retained-path>` below the isolated job prefix. These are
internal staging paths only. They must not be presented as importer-ready output.
Plans support at most 100 materials, 20,001 objects and 256 GiB total, subject to the
stricter per-object/path limits in `gcs-staging-transport.md`. Empty retained files
block this version.

`completion_manifest` requires exactly one matching receipt for every planned object,
including its destination, input hash, size and digest. It emits deterministic bytes
for `_reawote/complete.json`, with each object's explicit generation/metageneration.
The marker cannot include itself; its own verified upload receipt belongs in the
future durable completion record. Creating these bytes does not write a marker or
prove current approval. The coordinator must supply server-loaded approved batch
items and accepted packaging observations, compare the complete plan again before
dispatch, record receipt provenance and fence finalization. Neither a client-supplied
plan nor fabricated receipt DTOs constitute authorization or remote verification.

## Authenticated preview

POST `/api/publication-batches/{batch_id}/staging-preview` requires current ADMIN or
LEADERSHIP access and existing CSRF/session protection. Supply a proposed UUIDv4 job
ID, expected batch snapshot/CSV hashes and exactly one material/execution/observation/
proof selection per batch item. No caller-supplied CSV, file paths, reports or target
URLs are accepted. An explicit server bucket/prefix is needed; preview can run with
GCS disabled and does not consume credentials.

The server locks all materials in UUID order before folder/brand locks, reconstructs
the immutable batch, checks every accepted packaging observation and current input/
policy/approval context, then compiles the plan. It bounds aggregate proof loading to
64 MiB before JSON decoding and allows one preview per API process. The response
contains a plan hash, counts, bound material references and at most 20 object entries;
it exposes neither source inventories nor raw worker requests or credentials.

This read-only business operation creates no job, dispatch, audit, GCS object or
publication state transition. Authentication may update ordinary session activity.
Later material changes can block staging while historical package downloads remain
valid. The preview makes no new NAS observation; a future dispatch must recheck live
source/current authorization and record durable ownership before external IO.
`importer_compatible` is always false for this internal profile.

## Verification and remaining work

`tests/test_gcs_batch.py` checks complete coverage, deterministic output, provenance
changes with unchanged bytes, structurally valid but incorrect worker layouts,
missing/duplicate/substituted receipts and mutated plans. Application tests cover
current roles, exact selection, changed content, missing configuration and proof
bounds without any external IO.

Final affected-area Linux run: 364 passed, no skips. Actual PostgreSQL suite:
215 passed, auth 27/27, no skips, including material/brand edits waiting for preview
locks and then invalidating the next preview. Windows targeted preview/compiler/access
run: 80 passed. No migration or frontend change was required for this slice.

Durable upload reservation, fencing, attempt/receipt history, safe recovery, UI,
actual importer mapping and manual-import confirmation remain separate work. No live
GCS, Notion or production operation is enabled by this preview.
