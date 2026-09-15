# PBR technical validation

`POST /internal/material-validate` is a read-only worker operation. It inventories
the entire linked tree before and after checking the highest root resolution.
It returns a versioned report tied to that inventory hash, never an approval.
Source bytes, timestamps and names are unchanged.

## Checks and limits

- Names use the current technical identity, canonical shortcut and resolution:
  `<prefix>_<NNNN>_<category>_<MAP>_<n>K.<extension>`.
- Supported shortcuts: AO, COL, DISP16, DISP, GLOSS, NRM16, NRM, ROUGH.
  Duplicate shortcuts, nested master entries and unknown names block approval.
- PNG, JPEG, TIFF and WebP are actually decoded. Extension and detected format
  must agree. COL is required, its largest side must be at least 1024 pixels,
  all maps must match its dimensions, and shortcuts ending in 16 need 16 bits.
- Multi-frame, floating point and CMYK images are rejected. Palette and grayscale
  images are readable inputs; map semantics still need a human technical review.
- A declared master resolution that differs from actual COL dimensions is a
  warning, preserving the historical compiler's effective-resolution behavior.
- Missing normal/surface-response maps or previews are warnings. Existing root
  `metadata.txt` parser warnings, including missing/invalid metadata, remain
  nonblocking. No raw metadata or parser diagnostics appear in this report.
- Each image is opened through an anchored no-follow descriptor. A child process
  receives that descriptor, a minimal environment and no filename. It verifies
  and fully loads the image using [Pillow's documented API](https://pillow.readthedocs.io/en/stable/reference/Image.html).
  Its SHA-256 must match the inventory. The child has 2 GiB address space,
  30 CPU seconds, no core dumps and at most 35 seconds wall time. Images are
  bounded to 16384² pixels and 32768 pixels on a side. A master has at most 64
  entries; the full validation has a cooperative 120 second deadline.
- One validation runs per worker process; overlapping calls return
  `503 VALIDATION_BUSY`, without waiting in a decoder queue. Run one worker
  process per container unless the deployment memory budget is increased.

The observed tree is not a filesystem snapshot. A file may change after the
response. Approval must revalidate; packaging must copy into an owned immutable
staging area and verify every approved input hash. This operation does not
generate packages, write sources, grant human approval or publish anything.

Verification uses generated test images only, including real PNG/16-bit PNG,
JPEG, WebP, TIFF/16-bit TIFF, corrupt/truncated files, invalid modes, duplicate
maps, source races and descriptor/timeout/diagnostic boundaries. The Linux test
container has no network, no mounted source data and a read-only root filesystem.
