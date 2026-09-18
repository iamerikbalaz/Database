# Durable local packaging results

`worker/app/packaging_store.py` retains a verified live assembly outside the source
and temporary workspace. It delivers the established asset layout: root web
`metadata.json`, one ZIP per resolution and PREVIEW files/directories. Production
`metadata.txt` remains inside each ZIP. Preview delivery reads the first already
verified ZIP, so retention never rereads mutable NAS sources.

The artifact root must already exist, be private to the worker UID and have no
symlink ancestors. It must be disjoint from both the material root and staging
root in both directions. Operations use UUIDv4 names, a private operation lock and
the existing fsync/atomic `FileJournal`. Existing unknown directories are preserved
and refused. This storage module is used by [execution](packaging-execution.md),
the [private service](packaging-service.md), application [actions](packaging-actions.md)
and [downloads](packaging-downloads.md). It does not independently authorize a job,
upload or publication.

## API and integrity

- `retain_packages(bundle, artifact_root=..., request_hash=...)` writes a complete
  live bundle and returns a `StoredPackages` result. The caller supplies the stable
  digest of the authorized request; it is bound together with the plan digest.
- `recover_packages(artifact_root=..., operation_id=..., request_hash=...,
  plan_hash=...)` reconciles recorded output after a restart without reading NAS
  inputs. It never builds missing images or treats incomplete bytes as complete.
- `cleanup_incomplete_packages(..., expected_root_identity=...)` is an internal
  execution-owned cleanup step. It verifies retention under its existing lock,
  returns a complete result if available, or removes only a proven incomplete
  incoming copy. It preserves the original journal/proof and treats an absent
  operation as a no-op. It is not an exposed general delete endpoint.
- `open_retained_file(..., expected_proof_sha256=..., path=...)` reads an exact
  allowlisted file against a proof held independently by the caller. Files are
  opened read-only through no-follow descriptors and checked for bytes, size,
  private ownership/mode and replacement during reading.

The retained proof contains the original assembly proof, delivered file hashes
and directory names. Its hash differs from the assembly proof hash because it
also covers the delivered layout. The journal stores provenance and digests, not
raw production metadata, source contents, credentials or process diagnostics.
Files are copied exclusively, read back and hashed, frozen to 0400 and flushed.
The directory tree is checked against the exact manifest before completion.

Exact READY retries return the original retained result, even if a later assembly
of the same inputs would have different output timestamps. Request/plan mismatch
under the same operation UUID is a conflict. Initial and replayed responses use
the same JSON container types; returned documents are detached from journal state.
READY files are immutable through this service. No generic deletion/expiry API is
implemented.

## Durable transitions

| State | Recorded fact | Recovery behavior |
| --- | --- | --- |
| RESERVED | Request, plan and output proof are durable. | An absent incoming directory can be rebuilt by an authorized live bundle; an unrecorded existing directory requires operator review. |
| BUILDING | The incoming directory's device/inode is recorded before copying. | Complete verified bytes can finish; a known partial copy remains unavailable and can be rebuilt from a matching live bundle. |
| READY | The complete directory has been atomically renamed without replacement and the final journal is durable. | Verify and return the original result. |

A crash after the no-replace `incoming` to `ready` rename but before the last
journal update is reconciled by checking directory identity, exact entries and
all file hashes, then recording READY. A known incomplete incoming tree may be
removed only under its operation lock and recorded identity. A rebuild records
the prior incomplete attempt and proof hash; at most 32 attempts are allowed.
Unexpected entries, symlinks/hardlinks, directory replacements, full-size corrupt
files and ambiguous reservation gaps are preserved and refused. Recovery never
overwrites those conditions to force success.

Execution invokes incomplete-copy cleanup after a handled retention failure and
during explicit reconciliation of incomplete work. Complete incoming output is
recovered to READY, never discarded. The temporary attempt workspace is cleaned
first on failure, so an unproven retained tree does not keep those temporary copies.
The cleanup has a separate maximum 120-second verification/removal budget and the
existing 50,000-entry/depth-20 walk limit. Refused or interrupted cleanup leaves
the original BUILDING journal valid; another explicit recovery can inspect the
remaining subset. Only a new authorized attempt regenerates output and records
the old proof as INCOMPLETE history. Missing READY bytes remain an integrity error.

The retention lock now verifies its own descriptor/path signature, operation/root
identities and exact operation-directory entries. These location checks also run
during incomplete-tree removal. The caller supplies the artifact root identity
already bound by its execution journal; a replacement cannot be adopted for cleanup.

Default retention limits are 600 seconds and 16 GiB of delivered files, with a
32 MiB journal/proof bound. The underlying container/storage must also enforce its
own quota. Locks are nonblocking. Downloads hold their operation lock while open;
callers must provide their own bounded transfer lifecycle. READY reads do not
rewrite the journal or file timestamps.

## Verification and remaining boundary

Tests use only owned synthetic Linux folders and actual assembled packages. They
cover persistence after temporary cleanup, exact/conflicting retries, a new bundle
with changed timestamps, all copy/commit interruption points, a real process exit
between rename and READY, replay after restart, known incomplete rebuilds, unknown
reservation gaps, root overlap, link/ownership attacks, physical write corruption,
replaced directories, changed artifacts/journals, operation locking and bounded
work. Run `scripts/test-packaging.ps1` for the complete required-runtime suite.
The original retention checkpoint passed **562 tests**, including all 46 retention
cases, in 207.71 seconds, with no skips and two existing dependency deprecations.
All 11 offline runner isolation checks also passed. See the checkpoint in
`autonomous-pbr-history.md` for the original owned test project; the current
`autonomous-pbr-progress.md` records later complete-suite checks.

Initial tests found tuple/list differences between newly returned and JSON-replayed
proofs; normalization fixed all affected replay cases. A subsequent expected-name
assertion was corrected to the source fixture's lowercase Czech letter; stored
filenames were already preserved correctly.

The [execution layer](packaging-execution.md) now recovers recorded temporary
staging/assembly orphans under its inherited lease and cleans ordinary failures.
Durable database jobs/attempts, saved historical policy and audited overrides,
current authorization/source/approval checks, operator controls and configurable
[GCS staging](gcs-upload-jobs.md) are also implemented. A READY worker artifact
still does not establish upload or publication.

Accepted retained artifacts have no expiry/deletion API yet. Partial retention has
the guarded execution-owned cleanup above. Accepted-output cleanup after upload/
failure still needs an explicit lifecycle compatible
with immutable job evidence, historical downloads and active transfer locks.
Sudden power-loss behavior on production NAS, multi-gigabyte throughput, actual
importer compatibility and explicit online-import verification remain unverified
or unfinished. No live external publication has been performed.
