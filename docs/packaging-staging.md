# Private packaging inputs

`worker/app/packaging_stage.py` implements a Linux-only context manager around
short-lived, verified input copies. It is not an HTTP endpoint or a publication
job. Its application caller must hold a durable authorized material operation and
recheck the current approvals before committing artifact results.

The function rebuilds the approved plan, verifies the complete source inventory
before and after copying, and reserves a new `packaging-<UUID>` directory. Its
configured root must already exist, be private to the worker UID, contain no
symlink ancestors, and be disjoint from the material root. Existing operation
directories are refused without alteration.

Only reviewed maps, production `metadata.txt` and PREVIEW entries are copied.
Source files are opened read-only through anchored no-follow descriptors; unsafe
links, nonregular files and source replacements fail closed. Copying has byte and
time limits. Each target is read back and hashed before being frozen to mode 0400.
The consumer receives allowlisted read-only descriptors whose bytes are checked
again on opening. Errors use fixed codes without paths or source contents.

Normal exit and failure remove only the reserved workspace, using descriptor
relative cleanup with inode, device, depth and entry limits. Inserted symlinks
are unlinked without following their targets. A replaced workspace is preserved
and reported as `PACKAGING_STAGE_CLEANUP_REQUIRED`, never silently deleted.
Consumer handles expire when the context closes.

This is process-local staging, not durable recovery. A killed process or failure
between directory reservation and opening can leave a private orphan. The
[execution journal](packaging-execution.md) now supplies guarded orphan recovery
and ordinary failure cleanup around this context. Conversion, ZIP creation,
retention, [job actions](packaging-actions.md) and [GCS staging](gcs-upload-jobs.md)
are connected in separate layers. The private UID
and runtime isolation remain required; this is not a sandbox against a hostile
process with the same UID or host administrator privileges.

Verification uses only synthetic folders in a network-disabled Linux container
with a read-only root and bounded temporary storage. The 31 staging cases cover
stale and changing inputs, symlinks/hardlinks/FIFOs, source ancestor replacement,
partial/corrupt copies, allowlist and lifetime restrictions, redacted errors,
private roots, bounded work, replaced workspaces and cleanup without source
content/mode/mtime changes. No production filesystem was used.
