# Source folder discovery

The conservative contract is a read-only, explicit
one-level directory browser beneath the worker's configured MATERIALS_ROOT.
There is no recursive NAS crawl and no automatic linking or import. An empty
parent path means that configured root; arbitrary roots and traversal are rejected.

The backend restricts browsing to ADMIN and reauthorizes after worker I/O.
Selecting an exact identity match only fills the existing folder form; the
normal preflight/link operation remains required. Names are not evidence that a
folder contains valid material data. No production NAS verification is claimed.

Worker limits: 4096 total entries, 512 directory results, 2048 UTF-8 path bytes,
16 components, five-second cooperative deadline and two concurrent operations.
Limits reject the whole response, with no partial success. Plain non-directory
entries, links, mount boundaries and unsafe/unrepresentable names are omitted
and counted. Files are never opened; child directories are not scanned. Listing
consistency and the requested descriptor chain are checked again before return.
The deadline cannot interrupt a blocked operating-system filesystem syscall;
the client timeout and deployment-level storage health remain necessary.

No schema migration is needed. Only synthetic, owned Linux directories are used
for validation. Production folders, source contents and external services are
outside this slice's write scope.

## API and user interface

`POST /api/materials/{id}/folder-discovery` accepts exactly `{parent_path: string}`.
An active administrator session, completed password change, CSRF token and trusted
Origin are required. The material must exist and have no active identity operation.
The response contains the requested parent, material UUID/identity, directories
with relative paths and exact-name match flags, and an omitted-entry count.
It never contains file contents or absolute server paths. Worker transport is
bounded to 4 MiB, rejects redirects and duplicate JSON fields, and binds the reply
to the requested parent. Source findings and errors are withheld until fresh
account/material checks succeed after the worker call.

The material's Folder connection panel contains the administrator source browser.
Open it, list the root or enter a known parent, and navigate with Open/Parent/Root.
Results support local filtering and pages of 50. Use this folder appears only for
an exact identity match. It clears any previous preflight and fills the normal
form; Check folder and the explicit link confirmation are still required. Merely
browsing or selecting does not change the material, its metadata or approvals.

## Verification checkpoint

New local backend API/transport tests: 29 passed. New frontend tests: 28 passed;
lint and build passed after correcting a test-only type import. Linux worker,
actual PostgreSQL concurrency and fresh/retained browser checks are complete below.
The first worker attempt passed 23/24; the remaining test's HTTP client could not
encode a lone surrogate before sending it. It now sends escaped JSON so rejection
is verified by the worker. The first browser attempt passed 13/14; the new test
looked for an identity inside only the match badge. It now checks the whole row.
No production validation, access checks or browser safety gates were relaxed.

Final isolated Docker project `reawote-test-b9a02bf93fd144d988cf2f698d01b57b` passed
883 backend, 137 actual PostgreSQL (mandatory auth gate 27/27), 354 Linux worker
and 352 frontend tests; lint/build passed and no tests were skipped. The owned
frontend image was refreshed before its checks to include the test type-import
correction. Fresh/prior-schema migrations and Alembic current/heads/check passed.
Owned containers/network were cleaned and the exact owned database volume retained.

Final E2E run `0969494a-23b5-48b7-9fd1-5105fb2ad6f1` passed all 14 scenarios on
fresh data and all 14 after restart. The browser opens the source root, descends
to the synthetic library, selects the exact identity and proves the selection
made no database link. The normal preflight/link/Done flow then succeeds. Desktop
and 390px screenshots were visually inspected; no horizontal document overflow.
Protected project/volume checks passed. Real NAS layout and data remain unverified.
