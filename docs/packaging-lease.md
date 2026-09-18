# Packaging execution lease

The forthcoming job executor must retain ownership while conversion descendants
still use its temporary files. Closing or killing only their supervisor must not
allow recovery to delete or rebuild a workspace underneath ImageMagick.

The Linux-only packaging_lease module opens a fixed execution.lock in an already
owned private journal directory. Initial creation is exclusive; reuse requires the
independently persisted device/inode/ctime identity from the first lease. Existing
unknown/replaced locks, links, nonregular/nonempty files and nonprivate ownership
or permissions are refused. Locks are exclusive and nonblocking. The caller must
also validate the journal directory's recorded identity and named root binding.

The active lease is local to the Python execution context. Runtime verification,
image probes and conversion explicitly pass its descriptor to child processes.
The fixed conversion child retains that descriptor when exec replaces it with the
real ImageMagick binary. No token, environment variable, source path or arbitrary
executable argument is added. Calls outside an execution context keep their
previous descriptor behavior.

A context closes its own descriptor and never issues LOCK_UN. Inherited descriptors
refer to the same Linux open file description, so the kernel keeps the lock until
the last descendant closes it/exits. Normal conversion still waits for completion
or kills and reaps its own process group on deadline/failure. Nested leases are
refused; execution cannot silently switch its ownership context.

## Verification

The focused required-runtime Linux run passed 15 tests in 1.81 seconds, no skips.
It includes an actual SIGKILL of a supervisor while its descendant remains alive;
an independent open remains locked until the descendant exits. Another test
observes the descriptor inside the real ImageMagick process after exec, while
ImageMagick waits on a synthetic pipe, and verifies the same lock lifetime. The
public conversion API still accepts only verified regular source files.

The four actual conversion subprocesses (runtime, input probe, conversion and
result probe) all inherit the lease. Link/permission/replacement attacks, private
directory validation, normal/error cleanup and context reset are covered.

This is a prerequisite for execution-journal/workspace recovery. It does not yet
provide a packaging HTTP endpoint, database job, orphan cleanup or live publication.
The complete required-runtime worker regression passed 577 tests in 204.51 seconds,
with no skips and two existing dependency deprecations. See autonomous-pbr-progress.md
for the isolated run identifier and retained test image.
