# Source inventory contract

The worker's read-only `POST /internal/material-inventory` accepts the same
relative `folder_path` as preflight. It returns version 1, the folder basename,
master resolution/time, existing ZIP policy, ordered entries, total file bytes
and a SHA-256 source revision. Entries contain relative path, file/directory kind,
size and file SHA-256. File contents and host paths never leave this endpoint.

The revision is SHA-256 of UTF-8 JSON containing exactly `schema_version`,
`folder_name`, `master_resolution`, `policy` and `entries`, sorted object keys,
ASCII escapes and separators `,` and `:` without spaces. Entries sort by path.
Empty directories are included. File timestamps do not affect the revision;
the master directory timestamp affects it only through the existing ZIP policy.
All source content is included conservatively, including metadata.txt, all map
resolutions, SOURCE and PREVIEW. Adding, removing or renaming files invalidates
the revision. No generated output may be written into this source tree.

Linux descriptor-relative no-follow access is mandatory. Links, hard-linked
files, device files/FIFOs, cross-device entries and unsafe names are rejected.
Each descriptor and directory entry is checked before/after reading; a second
stat walk and re-open of the requested material detect replacement or changes
during the scan. All failures return a safe code and no partial inventory/hash.
The preflight's existing nonblocking treatment of metadata remains unchanged.

Limits: 20,000 entries, depth 16, 64 GiB per file, 256 GiB total and a 120-second
cooperative deadline checked between filesystem operations. This deadline does
not interrupt a blocked OS/NAS read. Files stream in 1 MiB chunks.

This is an observed revision, **not an atomic filesystem snapshot or lease**.
External edits can occur after any scan. Approval must rescan and match the
reviewed revision; publication must copy from validated descriptors into its own
immutable staging area and verify every copied hash before using approvals.
An inaccessible or changing source must never be treated as approved/current.

The endpoint is internal; normal clients use the authenticated backend. Worker
ports must not be exposed beyond the local/internal application network.
This slice supplies inventory only. Technical image checks, persisted inventory,
approval workflow and publication consumption are subsequent work.
