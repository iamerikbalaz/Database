# Verified ZIP writer

`worker/app/packaging_zip.py` writes an explicit, bounded entry list into an
exclusively reserved private descriptor. The caller supplies allowlisted read-only
descriptors for already verified input copies. This component does not select
assets, validate image pixels, change source files, persist jobs or upload data.

The archive has one named root and explicit directory entries. Relative paths,
parent directories, sizes and hashes are checked before writing. Duplicate names,
case-insensitive collisions, file/directory collisions and traversal are refused.
Files use ordinary DEFLATE level 6, directories are stored, and ZIP64 is available
when required. UTF-8 names, including Czech names, are preserved. Portable archive
permissions are 0644 for files and 0755 for directories; source permissions are
unchanged. Each source is hashed while streaming, with size, mode, link count,
ownership, modification time and open-file signature checked.

Legacy policy records `2026-01-01 00:00:00` for every ZIP entry. Current policy uses
the actual staged file/directory modification time in the explicitly supplied
storage timezone, rounded down to ZIP's two-second precision. Neither policy
calls `utime` on sources. ZIP timestamps do not encode a timezone; the returned
proof records the selected timezone and policy separately.

After finalizing the ZIP, the writer reads every entry again. It checks the exact
ordered names, dimensions of the archive (entry sizes/count), entry timestamps,
compression methods, attributes, flags, CRC and content hashes. Unexpected ZIP
metadata, missing/extra entries, trailing junk and corrupt compressed streams are
refused. The completed archive receives a SHA-256 proof and is frozen to mode
0400 only after verification. File contents and diagnostics are not returned in
errors. No extraction is performed.

Writing is bounded before each write; reading limits central-directory memory
and streams expanded files in chunks. Defaults are 8 GiB compressed, 16 GiB total
expanded and 600 seconds. The enclosing runtime must still provide private storage
and process limits. The caller owns output cleanup after failure and durable
artifact retention after success. Full resolution assembly remains the next step.

The implementation uses the standard [Python 3.13 ZIP API](https://docs.python.org/3.13/library/zipfile.html).
Tests cover streaming reads/writes, actual CRC/hash checks, both policies, explicit
timezone conversion, exact opaque metadata/preview bytes, empty files/directories,
Unicode, input races, collisions, link/mode restrictions, compressed/expanded/time
limits and deliberate archive corruption. A small ZIP64 fixture lowers the test
threshold to exercise the format; multi-gigabyte throughput and production golden
archives remain unverified.

On 2026-09-18, all 35 ZIP tests plus 62 planner/staging cases passed in a
network-disabled, nonroot Linux container (97 total, 0.86s, no skips/warnings).
The first ZIP run found one unnormalized `zlib.error` on deliberately corrupt
compressed data; it now returns the fixed failure code, with the failing test
retained. Run `scripts/test-packaging.ps1` for the full worker/converter suite.
