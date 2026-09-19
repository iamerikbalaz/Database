# Durable packaging results: implementation plan record

Local assembly is complete and tested; its context removes temporary artifacts.
The worker retention implementation now follows this plan; its concrete contract
is in [packaging-retention.md](packaging-retention.md). Subsequent database job,
download and retirement integration is tracked in the
[current checkpoint](autonomous-pbr-progress.md). The sequence below records the
original dependencies; this plan does not enable source writes or publication.

Use a separately configured existing private artifact root, disjoint from the
material root and ephemeral workspace. Bind a per-operation journal to the UUID,
caller request digest and plan digest. Serialize the operation with a nonblocking
Linux file lock; reuse the existing fsync/atomic-journal primitives where suitable.
An existing directory without a valid journal is an unknown collision, never an
invitation to delete or overwrite it.

Write into an exclusively reserved `incoming` directory whose device/inode is
recorded before copying. Preserve the legacy delivered structure: root web manifest,
per-resolution ZIPs and PREVIEW entries. Preview delivery can copy from the first
already verified archive, so it never rereads a mutable NAS source. Every retained
file must match the bundle's size/hash proof. Store the full proof document and its
hash, but no raw metadata or diagnostics in the journal. Bound entries, bytes,
manifest size and time. Flush files and directories before the final transition.

Only an atomic no-replace `incoming` to `ready` rename followed by a durable READY
journal can make a result available. Recovery must handle a crash between rename
and journal update by verifying the recorded directory identity and every byte.
An incomplete incoming result may be discarded only under its operation lock and
recorded owned-directory identity. It is never exposed as ready. A rebuild keeps
the same immutable request/plan inputs and records the attempt; mismatched inputs
cannot reuse the operation ID. Exact READY retries return the original frozen
result even if later assembly timestamps would differ.

Reading retained output requires a matching expected proof hash, allowlisted
artifact name, no-follow descriptor traversal and fresh content verification.
Retained READY results are immutable through this service. No generic deletion or
retention-expiry endpoint is part of this slice.

Tests must inject failures during each copy, before/after directory rename and
before the READY journal write, then recover after process restart. Cover exact
retry/conflict, concurrent same-ID work, unknown collisions, altered journal/proof,
symlink/hardlink/replacement attacks, changed or missing artifacts and refusal to
write into sources/workspace. Use only owned synthetic folders and the isolated
packaging runtime.

After worker retention: additive database jobs/attempts, first-policy persistence
and audited overrides, current approval/source rechecks, restricted operator UI,
GCS adapter and importer confirmation. Material identity/source ownership must be
coordinated before exposing execution. An artifact on disk alone is not a completed
or published database job.
