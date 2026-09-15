# Controlled identity operations: current implementation checkpoint

The worker's `POST /internal/material-identity-plan` is read-only. It accepts
`folder_path`, `target_path`, `brand_name` and `material_name`, inspects the complete
source tree and target parent, and returns the old/new root paths, every affected
relative file/directory path, metadata field names and before/after hashes,
blocking errors, warnings and a deterministic plan hash. It performs no rename,
file creation, metadata write, number reservation or database update.

`ready` means the observed plan has no blocking finding. It is not authorization
to execute it and is not a filesystem lease. The confirmation path must recreate
the exact plan, compare its hash and journal every mutation before enabling writes.
Backend plan orchestration, confirmation and UI are pending. The recoverable
executor below is implemented as an internal module, with no enabled HTTP source
mutation endpoint until that coordination exists.

## Conservative rules

- The current identity remains `<prefix>_<NNNN>_<category>`. Display-name edits
  alone do not alter that identity. Plan targets use portable ASCII identity
  components, sequence 0001–9999 and existing category character rules.
- The destination parent must already exist under the allowed root on the same
  filesystem. No parent links, path traversal, nested self-moves or overwrite
  collisions are allowed. Case-folded collisions are rejected for portability.
- Renaming applies to path components equal to the old identity or beginning
  with that identity followed by `_` or `.`. Other source names are preserved.
  The result lists inherited parent-directory changes too.
- Root `metadata.txt` may contain the observed dimensions-only text format, which
  stays byte-exact, or strict source JSON. Only existing documented identity fields
  and source references are transformed. Unknown values remain unchanged and
  JSON number tokens retain their exact precision and exponent notation.
- An inconsistent existing identity, ambiguous/unrecognized metadata, duplicate
  JSON keys or an old identity left in an unmapped reference blocks execution.
  Missing metadata is an explicit warning; planning does not invent a document.
  Source contents and old/new raw metadata never appear in the plan response.

Linux verification: 236 worker tests passed, no skips, including collisions,
target-parent links, unchanged source bytes/timestamps, metadata privacy and
exact JSON numbers. Earlier portable run: 15 passed, 8 POSIX tests skipped.

## Next implementation constraints

Durable journal primitives now use private per-operation directories, a
nonblocking Linux file lock, fsync and atomic state replacement. Pending files are
checked for type, ownership, mode and hard links before any truncation. Linux
`renameat2(RENAME_NOREPLACE)` preserves the source inode and refuses an occupied
destination. The full worker suite passes 251 tests with no skips, including
interrupted state writes and symlink/hardlink attacks; portable primitive checks
pass 8 with 7 POSIX skips. These primitives are not yet an enabled source-write API.

`identity_execute.execute_identity_change` requires explicit `enabled=True`, an
operation UUID, the expected plan hash and a separate private journal root. It
recreates the plan, records an immutable request fingerprint, moves the material
through a temporary root, renames affected components through collision-free
temporary names, replaces known metadata atomically and verifies the complete
resulting inventory. File inodes and permissions remain intact for renames;
rewritten metadata retains its owner/group/mode, and resolution-directory mtimes
are restored so renaming does not change the historical ZIP-policy boundary.

Each step is checkpointed before execution. Failure reverses applied steps and
restores original metadata bytes and mtimes. An interrupted operation is rolled
back on retry using the same journal. A complete/rolled-back request is idempotent.
Conflicting inputs cannot reuse its ID. Conflicting external data is preserved and
returns RECOVERY_REQUIRED; the executor never overwrites it to force completion.
Private metadata backups remain in the journal for controlled recovery.

Full Linux worker verification: 276 passed, no skips. New cases inject failure
after all 11 representative steps, interrupt the process at different stages,
force partial metadata writes and concurrent metadata edits, and replace the
target directory after completion. No mutation endpoint is enabled by these tests;
all source fixtures live in the container's disposable tmpfs.

Confirmed operations must use durable idempotent journals, no-replace renames,
exclusive material ownership and recoverable state across backend/worker crashes.
Only after verified filesystem completion may database identity change. A rebrand
must burn a new target-brand number even if execution later fails. Published
identity changes remain blocked until online importer matching is verified.
Validate filesystem behavior exclusively with owned synthetic folders.
