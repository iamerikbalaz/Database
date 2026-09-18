# Complete local package assembly

`worker/app/packaging_assembly.py` connects the approved plan, private input
staging, actual image conversion and verified ZIP writer. It yields a complete
short-lived bundle with one archive per planned resolution and a root web
manifest. It performs no database writes, upload or online publication.

## Execution and provenance

Keep `stage_packaging_inputs(...)` open, then call
`assemble_packages(staged, workspace_root=..., storage_timezone="Europe/Prague")`.
The output root must be the exact path and directory inode of the private root
already checked by staging. A new `artifacts-<operation UUID>` directory is
reserved exclusively. Existing directories are refused without alteration.

The effective master is created first. An exact square master is copied byte for
byte and independently decoded again; rectangles and adjusted masters use the
bounded converter. Every lower resolution reads the verified generated effective
master, retaining that exact input digest in its map proof. The generated manifest
therefore describes image dimensions actually verified during assembly. It keeps
the established keys, map list and height/width ratio.

Each ZIP contains its single `<identity>_<resolution>/` root, that resolution's
maps, unchanged `metadata.txt`, PREVIEW entries and `metadata.json`. Missing
optional metadata/previews remain explicit plan warnings. SOURCE stays outside
the package. ZIP entry timestamps follow the explicitly supplied saved policy;
current file timestamps come from staged/generated files and virtual directory
entries receive their construction time in the selected storage timezone.

The returned bundle binds the operation UUID, full plan, source inventory hash,
policy, timezone, manifest hash, map conversion proofs and all archive proofs.
`proof_sha256` hashes that complete provenance. `open_archive(filename)` and
`open_manifest()` yield read-only descriptors after checking actual bytes and
private file attributes again. They expire when the context closes. Arbitrary
map paths and filenames cannot be opened as archives.

## Limits, lifetime and recovery boundary

One assembly may be active per worker process; additional calls receive BUSY.
Default construction limits are 900 seconds and 16 GiB for generated maps,
manifest and archives combined. Each read/hash/conversion/ZIP step receives the
remaining deadline. Input staging and the converter cache have their own separate
limits; the container's storage quota bounds the combined physical usage.

Normal exit or exception removes only the exclusively owned artifact directory.
Incomplete conversion/ZIPs are never returned as a complete bundle. Original
sources, unrelated files and still-live input copies remain intact. Cleanup
refuses a replaced workspace and returns CLEANUP_REQUIRED, preserving the
replacement and moved original for operator review.

This is not durable artifact storage or a recoverable publication job. A killed
process can leave a private orphan. The next service must hold durable material
operation ownership, persist the first historical policy per asset, recheck
current source/approval facts, retain verified output atomically and recover
interrupted attempts. Current source changes after staging cannot rewrite frozen
copies; the final database/approval recheck is still required before accepting a
job result. No HTTP endpoint exposes this assembly yet.

## Verification

The first 20 complete synthetic integration cases passed in 62.30s, with no
skips/warnings, in the unprivileged network-disabled packaging runtime. They
exercise both policies, actual converted/decoded output, square-copy preservation,
nonstandard and capped masters, exact metadata/PREVIEW bytes, all ZIP contents,
effective-master provenance, source preservation, optional inputs, partial failures,
unsafe output selection, artifact tampering and ownership-bound cleanup.

The expanded suite also covers aggregate size, whole-operation concurrency,
shortened input-hashing deadlines and re-decoding a copied file even when a caller
forges a successful report. The final full worker run
`reawote-packaging-e962d1e639424f4a88f3b24431408e7f` passed 516 tests in 127.99s,
including all 23 assembly tests; no skips, two existing dependency deprecations.
Run `scripts/test-packaging.ps1` with ImageMagick mandatory to repeat. Production
golden assets, multi-gigabyte stress and the deployed online importer remain
outside the completed verification.
