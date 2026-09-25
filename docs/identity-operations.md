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
Backend orchestration, explicit confirmation, recovery and the detail-page UI now
implement that coordination. Source writes remain disabled by default. The new
authenticated worker mutation route requires separate explicit configuration.

## Conservative rules

- New identities use `<prefix>_<NNNN>_<material-name>_<category>`; persisted
  three-part identities remain readable. Imported names retain exact spelling.
  Rebrand/category proposals preserve an existing name component. Plan targets
  use portable ASCII components, sequence 0001–9999 and existing category rules.
  Ordinary display-name edits currently do not rename folders. The user's newer
  name-and-folder workflow is still pending; keep source writes disabled for the
  real-data catalog test until that narrower contract is implemented.
- The destination parent must already exist under the allowed root on the same
  filesystem. No parent links, path traversal, nested self-moves or overwrite
  collisions are allowed. Case-folded collisions are rejected for portability.
- Renaming applies first to the full identity, then its base name without the
  final category. A component must equal that prefix or continue with `_` or `.`;
  unrelated substrings are preserved. Backend proof validation applies the same
  strict mapping. The result lists inherited parent-directory changes too.
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

## Database coordination and UI (migration 0009)

- `POST /api/materials/{id}/identity-plan` is a read-only observed proposal. It
  derives the identity from the selected brand, next available four-digit number,
  preserved material-name component and category. The destination parent must already exist. Its proposal hash binds
  the entire worker plan, generation and old/new database contexts. Planning
  neither reserves a number nor creates an operation record.
- `POST .../identity-confirm` requires a production lead/admin, the displayed
  proposal hash/generation, an idempotency key, reason and warning acknowledgment.
  It recreates the plan and checks authorization again. Under row locks it records
  a RUNNING operation, invalidates reviews, and permanently reserves a rebrand
  number before asking the worker to modify the source. The DB transaction ends
  before filesystem IO. Same-brand category/folder changes keep the number.
- Active operations block material edits, folder linking, Done, reopen, inventory,
  technical checks and approvals. Source/target brand edits are also blocked.
  A separate folder-catalog lock prevents a new folder link from racing ownership
  of an overlapping source/destination tree. Read-only database detail/history
  remain accessible. Other materials may still reserve subsequent brand numbers.
- Verified COMPLETED results atomically update brand/number/category/identity/path,
  preserve UUID/project/name/assignment, clear current metadata and append identity
  history. Historical metadata and approvals remain intact. The material remains
  IN_PROGRESS and requires fresh technical checks. Once authorized source work has
  started, consistency finalization completes even if the initiating account is
  later disabled; this does not authorize any new user action.
- REJECTED is a durable worker outcome before any source action. ROLLED_BACK proves
  original source restoration. Neither recycles the reserved number. A timeout or
  invalid response leaves RUNNING ownership; RECOVERY_REQUIRED also stays locked.
  `POST .../identity-operations/{operation_id}/resume` (lead/admin) reuses the same
  journal. A repeated confirmation only returns its recorded operation. Recovery
  never force-overwrites external source data.
- `GET .../identity-operations` and `GET .../identity-history` return the latest
  100 records. Pagination for older history remains backlog. PostgreSQL makes
  number/history ledgers append-only and protects recorded authorization inputs
  and terminal operation outcomes against updates/deletion/truncation.
- The UI displays old/new paths, all affected file/directory names, metadata field
  names and hashes, warnings, number reservation and approval invalidation. An
  unknown confirmation outcome keeps the same key and freezes its inputs for retry.
  Other roles may inspect history but cannot plan/confirm/recover.

Conservative product decisions: only linked IN_PROGRESS materials can use this
operation. DONE requires an explicit reopen first. Published identities stay
blocked pending online importer verification. Unlinked category edits retain the
existing PATCH contract and now append identity history. Migration 0009 backfills
known current allocations without changing existing brand counter high-water marks;
unknown historical actors are represented as null, never invented.

Migration 0010 adds brand collections. A rebrand is blocked while old-brand
collections remain assigned; remove those assignments explicitly in Publication
content before preparing a new plan. Content and affected catalog changes are
blocked while a filesystem operation owns the material.

## Enabling in an isolated environment

Both backend and worker require `SOURCE_MUTATIONS_ENABLED=true` and the same
private `WORKER_MUTATION_TOKEN` (32–256 printable ASCII characters, generated with
secure randomness and supplied through environment/secret management). Never put
the actual token in a compose file, shell transcript, source tree or log. The
worker additionally needs `IDENTITY_JOURNAL_ROOT` pointing at an existing private
directory outside the material root. Keep its durable storage across restarts;
per-operation directories/backup files must remain private (0700/0600). The worker
must have the ownership permissions required to preserve source file ownership.
Mount only owned synthetic materials as writable during validation. Normal/demo
configurations keep writes disabled. Do not use these settings on production NAS
without a separately authorized rollout, recovery procedure and exclusive-writer
coordination. The application does not claim a filesystem lease against unrelated
NAS clients. Keep the worker on a private network; use TLS if the service boundary
crosses a trusted local network.

Rollback: migration 0009 refuses downgrade while an operation is RUNNING or
RECOVERY_REQUIRED. Reconcile durable database and filesystem state first. Completed
renames cannot be undone by a schema downgrade. Export/retain operation/history
records before removing the schema; keep journals and original metadata backups.
Counter high-water marks are not lowered by downgrade. Old migrations are unchanged.
