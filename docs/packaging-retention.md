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
and refused. No database job, HTTP endpoint, upload or publication is enabled here.

## API and integrity

- `retain_packages(bundle, artifact_root=..., request_hash=...)` writes a complete
  live bundle and returns a `StoredPackages` result. The caller supplies the stable
  digest of the authorized request; it is bound together with the plan digest.
- `recover_packages(artifact_root=..., operation_id=..., request_hash=...,
  plan_hash=...)` reconciles recorded output after a restart without reading NAS
  inputs. It never builds missing images or treats incomplete bytes as complete.
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
The final isolated Linux run passed **562 tests**, including all 46 retention
cases, in 207.71 seconds, with no skips and two existing dependency deprecations.
All 11 offline runner isolation checks also passed. See the checkpoint in
`autonomous-pbr-progress.md` for the exact owned test project and prior suites.

Initial tests found tuple/list differences between newly returned and JSON-replayed
proofs; normalization fixed all affected replay cases. A subsequent expected-name
assertion was corrected to the source fixture's lowercase Czech letter; stored
filenames were already preserved correctly.

This does not recover ephemeral staging/assembly orphans left by a killed process;
those need a guarded job-level cleanup policy. It does not prove sudden power-loss
behavior on production NAS, multi-gigabyte throughput or live importer compatibility.
Next: durable database jobs/attempts, first historical-policy persistence and
audited overrides, current authorization/source/approval rechecks, restricted
operator UI, GCS transfer and explicit importer confirmation. A READY worker
artifact is not an uploaded or published material.
