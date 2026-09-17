# Deterministic publication CSV foundation

`backend/app/publication_csv.py` converts validated frozen row snapshots to bytes.
It has no database query, file/network operation or HTTP endpoint. It does not
verify human decisions itself. The future authenticated batch service must first
freeze matching approved inputs as described in `publication-plan.md`.

The serializer preserves the handoff's exact column order:

```text
identity_name;name;description;credits;dimension;brand_identifier;categories;color;tags
```

Output uses UTF-8 BOM, semicolons, CRLF records and RFC4180-style quoting of
embedded semicolons, double quotes and newlines. Dimension is `WxH cm`, decimal
dot, without insignificant zeros or exponent notation. It rejects binary floats,
nonpositive/nonfinite numbers, precision beyond four significant decimal places
and values outside Numeric(12,4); it never rounds. Color must be uppercase
`#RRGGBB`, credits an integer from zero through 2147483647, and categories nonempty.
Description and tags may be empty with explicit warnings.

Category/tag values are normalized and deduplicated using the existing vocabulary
rules before `:` joining. Colons and controls within an individual value fail
validation. First display spelling is retained. Unknown fields, invalid names,
duplicate UUIDs or case-insensitively duplicate technical identities fail. A batch
contains 1–100 rows, sorted by technical identity then UUID for reproducibility.

The immutable result includes CSV bytes/SHA-256, per-material source revision and
approved-content context digests, a SHA-256 of every full row snapshot, and typed
warnings. Internal UUIDs/hashes never become extra CSV columns. Equal decimal
values have equal snapshot hashes regardless of insignificant input zeros. Input
lists become immutable tuples; later caller edits cannot change the artifact.

Formula-like cell prefixes generate `CSV_FORMULA_LIKE_VALUE` with affected column
names. The exact approved value is preserved for importer compatibility; adding
an apostrophe would change it. CSV quoting does not neutralize spreadsheet formula
execution. The later UI must display this warning and use inert text for previews;
operators should use the intended importer or a text viewer for these artifacts.
This is a conservative warning, not a claim that the importer executes formulas.

Verification: 44 focused tests passed on Windows and Linux, including boundary precision
beyond the Decimal arithmetic context, Unicode, quoting, byte/hash reproducibility,
deduplication, immutable snapshots, bounds and formula warnings. The existing
full Docker image predates these two serializer files. Linux verification used
that image with only the two new files mounted read-only, no network, read-only
root and bounded temporary filesystem. The first collector used the previously
installed package and could not find the new module; `python -m pytest` selected
the mounted source and passed all 44 tests. No application behavior was changed
for that runner correction. No migration or user export is
part of this foundation. Online importer acceptance, immutable database batches,
packaging and upload are still unfinished. No real material was exported.
