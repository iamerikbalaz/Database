# Observed historical packaging source

The tracked `scripts/texture-zip.zip` contains both historical `rename.sh` files
and `readme.txt`. They were read directly from the archive on 2026-09-15; no script
was executed against user data. The archive is unchanged.

The scripts are packaging tools despite the filename. Both expect existing
source/output directories, use Windows ImageMagick 7.1.1 Q16-HDRI and Info-ZIP,
inspect COL dimensions, adjust the effective master, generate lower 16/8/4/2/1K
resolutions, copy PREVIEW, generate a root web manifest, and create one ZIP per
resolution under `<asset>_<resolution>/`.

The legacy branch recursively applies `touch -t 202601010000` before ZIP. The
current branch omits that step. Neither deletes original sources in its normal
packaging loop; removal targets the derived per-resolution output directory.

Important compatibility constraints and defects to handle explicitly:

- Filename parsing uses fixed underscore positions (`cut -d_ -f4` for a map,
  field 5 for resolution), incompatible with arbitrary underscore-containing
  current prefixes. The application must preserve its current `origin/main`
  identity contract and parse the known identity as a whole.
- `genMetadata` picks the first JPEG for dimensions and lists JPG/JPEG/TIFF maps
  from 1K. Its trailing comma after `DESKTOP_APP_PART` makes invalid JSON. The
  specification explicitly requires fixing JSON syntax while preserving keys
  and meaning. Actual image facts should support the selected input formats.
- Oversized images are bounded by the declared master; undersized images use
  the largest side divided by 1024, rounded down, and resize preserving aspect.
  A matching rectangular master may still be re-encoded by the old script.
- Current-branch timestamps are actual copy/conversion/archive timestamps,
  not a general promise to preserve all original file mtimes. Golden tests must
  distinguish copied originals from newly generated images and directories.
- Shell `eval`, unquoted path expansion, broad `find` matching and log-variable
  typos are not suitable security primitives for new worker execution.
- Missing previews and conversion failures may be logged without stopping the
  old script; the new publication workflow must not report verified artifacts
  until all required outputs pass checks.

The archived source allows a controlled synthetic compatibility harness, using
the same image conversion runtime for old/new comparisons. No production golden
asset corpus or online CSV importer source has been identified. Those remaining
verification gaps must stay explicit; they do not prevent isolated implementation
and contract tests. Published identity changes remain blocked pending importer
matching verification, as required by the handoff.
