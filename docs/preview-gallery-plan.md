# Read-only preview gallery and comparison

Next bounded slice, after content approval (0011). No schema migration or source
write is needed. The handoff names gallery/comparison but does not define image
layout or limits; the following conservative defaults are explicit assumptions.

- Read only direct images in a linked material's `PREVIEW` directory. Support
  JPEG, PNG, TIFF and WebP; ignore other ordinary files and nested directories.
  Linked/reparse/special/multiply-linked files are rejected. No arbitrary file
  download, SVG rendering or source-map browsing is introduced.
- A bounded listing hashes eligible files. Rendering requires the selected name
  and exact displayed file hash, securely opens through directory descriptors,
  checks file/directory bindings before and after, and rejects a changed source.
- Decode in a resource-limited Linux child and return a fresh JPEG, at most 1024
  pixels per side, without source EXIF/comments/ICC metadata. Fail closed on
  unsupported platforms, corrupt/multiframe images and resource limits. The
  gallery never changes source bytes, names or modification times.
- Backend session/assignment checks happen before worker IO and again afterwards;
  active identity operations block reads. The browser only receives authenticated
  application image URLs, never worker addresses or absolute source paths.
- Start with a selected-image gallery and a two-material side-by-side comparison.
  A comparison uses the same server authorization for each material. Real image
  dimensions/aspect are preserved; these previews are for visual review, not
  color-calibrated or original-bit-depth analysis.
- Prove limits, symlink/hardlink/FIFO/race safety and metadata stripping on actual
  Linux images; verify API role/assignment/context races and malformed worker
  responses, UI failures, and fresh/retained E2E using synthetic preview images.

This slice does not claim completion of AI provenance, historical imports,
packaging, publication jobs or external integrations.
