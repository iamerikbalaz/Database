# Packaging layout foundation

`worker/app/packaging_plan.py` is a pure planning component. It performs no reads,
writes, image conversion, ZIP creation or external calls. There is no packaging
HTTP endpoint or completed publication job yet. It is separate from CSV preparation.

The input is a successful `pbr-images-1` technical report plus its expected approved
source inventory digest and an explicit saved ZIP policy. It verifies the canonical
inventory digest, reviewed map names/dimensions/bit depths/content hashes, bounded
paths, parent directories and case-insensitive collisions. Every master file must
be accounted for. Root production `metadata.txt` remains byte-identical in each
resolution directory; the generated root web manifest is `metadata.json`. Conflicting
existing per-resolution metadata is rejected. SOURCE and other source directories
are not archived. PREVIEW files and directory entries retain their relative names.

Observed legacy behavior is preserved in the plan: cap an oversized master to its
declared resolution, floor an undersized master to whole 1024-pixel units, then
generate lower standard 16/8/4/2/1K resolutions from the effective master. Copy an
exact square master; re-encode rectangles. Filenames use the whole known identity,
including prefixes containing underscores. The immutable plan records the original
map provenance and whether conversion consumes the already generated effective
master. Expected dimensions use positive integer rounding and remain subject to
verification against the actual converter. A valid JSON manifest preserves the
historical keys, six-significant-digit height/width ratio and sorted map shortcuts.

The selected historical policy participates in the plan digest. It is deliberately
an explicit input: a later job service must persist the first policy per asset and
audit any administrator override. Reobserving a folder timestamp cannot silently
replace that saved policy. Legacy timestamps are to apply only to staged outputs.

## Next execution step

Private isolated input staging is implemented and tested in
[packaging-staging.md](packaging-staging.md), and bounded ImageMagick 7 Q16-HDRI
conversion in [packaging-conversion.md](packaging-conversion.md). Continue by
assembling resolutions into distinct exclusive paths, verifying actual images,
manifest and ZIP entry bytes/CRC/hashes,
then return artifact proofs. Failure must not report success or alter source files.
ZIP writing/verification is now implemented in [packaging-zip.md](packaging-zip.md).
Complete ephemeral artifact assembly is implemented in
[packaging-assembly.md](packaging-assembly.md). Durable job integration remains.

Runtime design references (checked 2026-09-17): [ImageMagick security policy](https://imagemagick.org/security-policy/)
describes policy ordering, delegate/coder restrictions and resource limits;
[ImageMagick resizing](https://usage.imagemagick.org/resize/) describes aspect-preserving
resizing and integer output pixels. The archived scripts, application source and
`legacy-packaging-contract.md` supply the project-specific behavior. No production
golden asset comparison or live online importer verification has been performed.

The initial 31 pure tests pass on Windows and in an isolated Linux container
without network/database and with read-only input mounts. They cover the manifest example's
rectangular dimensions, nonstandard effective masters, both policies, immutable
inputs, precise archive names, metadata collisions, traversal, stale source hashes,
unreviewed entries and malformed reports. These tests alone do not prove pixel-level
compatibility with the historical converter.
