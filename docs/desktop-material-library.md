# Windows desktop material library

The native adapter connects an explicitly approved local `Test_data` library to
the material card. It supports folder and metadata opening, a destination picker,
previews, folder listings, controlled identity changes and root `metadata.json`
editing. It is enabled by application construction, not by an HTTP request or by
installing a package. Normal `create_app()` does not enable desktop access.

## Installation and application construction

Run the backend in the signed-in Windows desktop session with Python 3.13. A
Linux container, a remote worker, or a noninteractive Windows service cannot
provide this desktop UI. Install the optional image dependency from the repository
root into the backend's existing virtual environment:

```powershell
python -m pip install -e './backend[desktop]'
```

The `desktop` extra supplies Pillow for preview decoding. Keep the normal backend
authentication, database migrations and settings configuration. Source edits
additionally require the explicit `source_mutations_enabled` setting. The normal
settings validator still requires a private worker mutation credential when that
setting is enabled, even if the metadata and identity clients are injected native
adapters. Keep that credential in server configuration.

Construct one shared `LocalMaterialLibrary` instance and inject the capabilities
needed by the application. This example uses illustrative, operator-owned paths;
the source directory and journal parent must already exist:

```python
from pathlib import Path
from types import SimpleNamespace

from app.local_materials import LocalMaterialLibrary
from app.main import create_app


def build_desktop_application(settings, database, worker_client):
    library = LocalMaterialLibrary(
        root=Path(r"C:\REAWOTE\Test_data"),
        journal_root=Path(r"C:\REAWOTE\desktop-journal"),
    )
    return create_app(
        settings=settings,
        database=database,
        worker_client=worker_client,
        local_library=library,
        metadata_client=library.metadata,
        identity_client=library.identity,
        preview_client=library.previews,
        folder_contents_client=library,
        discovery_client=SimpleNamespace(listing=library.discovery),
    )
```

`local_library` enables desktop actions only. Metadata, identity, previews and
folder discovery have separate client arguments; omitting one retains that
client's normal worker implementation. The adapter does not replace technical
preflight, inventory, packaging or publication services. Those services must use
the intended source configuration too.

The desktop bootstrap must pin the approved `Test_data` path. The library class
accepts an explicitly supplied root and enforces containment; it does not discover
an approved root from its directory name. Never derive the root or journal from a
browser field. Bind the desktop backend to loopback and keep the configured
frontend origin explicit.

## Filesystem and journal boundary

- Use an owned local Windows filesystem. UNC/NAS paths and links into another
  library are not a way to extend this adapter's permitted root.
- The source root cannot be a drive root. Relative source paths are validated;
  traversal, drive prefixes, alternate streams and unsafe components are refused.
  Ancestors are opened and verified before source access. Reparse points,
  junctions, hard-linked files and unsupported entries are refused or omitted
  from read-only listings.
- Keep the journal outside the source tree; neither root may contain the other.
  Both must be on the same volume because metadata backups and replacement files
  use atomic handle-based renames. Startup verifies the volume identities and
  opens the existing journal parent before creating the journal directory.
- Restrict the journal's Windows permissions to the backend account and authorized
  operators. It contains operation receipts and private metadata backups. It is
  not a browsable material folder or an HTTP download directory.
- Source file handles deny concurrent writes and deletion while being verified.
  Renames use verified handles with replacement disabled. An occupied destination
  is a conflict, including one created after a preview.

Use a persistent journal path across restarts. Each operation has a UUID and an
immutable request fingerprint. A retry must keep the same UUID and request.
Metadata saves retain the prior bytes, verify the replacement and reuse a verified
temporary file left by an interrupted preparation. Identity changes record each
rename and its file identity; retry recognizes a rename that completed before its
receipt was saved and continues the same operation.

The backend updates database identity or metadata only after a verified completed
result. An uncertain operation retains ownership and requires reconciliation.
Use the existing resume action; do not delete its journal, change its request,
clear its database state, or manually overwrite conflicting files to force
completion. Preserve both roots and the operation ID for operator investigation.
The tests cover interrupted process steps and retries, not storage-device power
loss or an external administrator modifying the recovery journal.

## Desktop HTTP actions

All paths below start with `/api/materials/{id}`. The server reads the source path
from the authorized material record; callers cannot supply another filesystem
path to these endpoints.

| Method and suffix | Result and behavior |
|---|---|
| `GET /local-files` | `{absolute_path, can_open, can_move}`. An absent adapter or unlinked material returns a null path and disabled controls. |
| `POST /local-files/open-folder` | Starts Windows Explorer for the verified linked folder; returns `{opened: true}`. |
| `POST /local-files/open-metadata` | Starts the fixed Windows text editor for the existing root `metadata.json`; returns `{opened: true}`. A missing file must first be created by saving library data. |
| `POST /local-files/select-destination` | Opens one native folder picker and returns `{destination_path, target_parent}`. Cancellation returns both values as null. It performs no move. |
| `POST /check-data` | Returns `{report, issues}` from the basic local inspection. It does not approve, package or publish the material. |

Desktop calls require an active authenticated session and a loopback client.
Every `POST` requires the trusted Origin and the session's `X-CSRF-Token`.
Processors are limited to their assigned materials; leadership can inspect/open
visible materials. Destination selection requires ADMIN or PRODUCTION_LEAD.
Archived material visibility remains ADMIN-only. Active operations block new
conflicting source actions.

Long picker/check operations release the database transaction, then recheck
account status, role, assignment, folder and material revision before returning
either data or a source error. Desktop launches hold the material row lock until
the launch call returns. API responses use `Cache-Control: no-store`. The absolute
path is intentional, scoped desktop information; raw metadata and internal error
details are not included in these responses.

Common responses are `401` for a lost session, `403` for CSRF/role/remote-desktop
rejection, `404` for inaccessible materials, `409` for a changed material or local
file conflict, and `503 LOCAL_DESKTOP_UNAVAILABLE` when no adapter is installed.
The UI should reload permissions/data after a conflict and retain an active
operation's exact request for recovery.

## Name changes and choosing a destination

The native picker selects an **existing parent folder** inside the approved
root. The application derives the material's final folder name. Selecting the
root is represented by an empty `target_parent`; selecting outside the root is
rejected. The picker cannot create parents and does not accept arbitrary NAS
paths. Only one picker can run at a time; it has a bounded timeout.

Use `POST /identity-plan` followed by `POST /identity-confirm` for a move, rename,
category change or rebrand. The proposal binds the source inventory, destination,
name and database revision. Confirmation retains the existing ADMIN/PRODUCTION_LEAD,
unpublished, `IN_PROGRESS` and active-operation guards. A move within the same
brand keeps the material number. A rebrand reserves a new target-brand number.
The identity workflow updates matching filenames and supported JSON identity
references together. See [identity operations](identity-operations.md).

`material_name` is optional in the identity request. Ordinary material `PATCH`
rejects a changed name on a linked material with `IDENTITY_PLAN_REQUIRED`.
Names that normalize to the same technical folder name are currently unavailable
through this workflow: for example, changing only display-name capitalization
returns `IDENTITY_UNCHANGED` when the folder would stay unchanged. The UI reports
that conflict; it does not silently change the database name.

A brand name is also the metadata `MANUFACTURER` value. Renaming a brand with any
linked material, including an archived material, returns
`BRAND_SOURCE_REWRITE_REQUIRED`. A coordinated multi-material manufacturer rewrite
is not implemented. Unlinked brand renames and unchanged-name submissions remain
available. Renaming a parent company preserves existing linked brands and creates
the new same-name company brand if needed, without rewriting source metadata.

## Library save and basic check

The library editor saves Color HEX, width and height to root `metadata.json`.
Identity fields come from the database; unknown supported document content and
precise numbers are preserved. Unified saves also finalize publication content
in the same database completion transaction after verified source success. See
[editable source metadata](editable-source-metadata.md).

Opening the file in the external text editor does not create an API audit event.
After an external edit, reload the source before the next application save;
expected hashes prevent stale values from overwriting changed source bytes.

The basic check counts safe folder entries and bytes, reads metadata status and
checks preview availability. Its report is provisional: final map/ZIP validation
rules are not configured by this adapter. A report with no listed issues does
not set Done, Checked or Published and is not a publication-readiness result.

## Focused verification

The API suite uses a fake library and opens no desktop applications. Native tests
use synthetic directories only; they exercise real Windows sharing, junction and
no-overwrite behavior. From `backend`, use a new disposable test directory:

```powershell
$testRoot = Join-Path $env:TEMP ('reawote-desktop-' + [guid]::NewGuid().ToString('N'))
if (Test-Path -LiteralPath $testRoot) { throw 'Test directory unexpectedly exists.' }
python -m pytest tests/test_local_files_api.py tests/test_local_materials_windows.py tests/test_local_materials_concurrency_windows.py --basetemp $testRoot
```

Native tests are skipped on non-Windows hosts. Passing HTTP/fake-library tests
alone does not verify Windows filesystem behavior. Keep the test directory,
source library and journal separate from any actual catalog or NAS source.
