# Verified image conversion

`worker/app/packaging_convert.py` converts one planned RESIZE operation between
already-opened private descriptors. `Dockerfile.packaging` provides separate opt-in
[runtime and test targets](packaging-service.md). The ordinary worker image does
not install ImageMagick. [Execution](packaging-execution.md) and [application jobs](packaging-actions.md)
now connect conversion, ZIP creation, retention and explicit authorized commands.

## Input and output contract

The source must be an owned, single-link regular file opened read-only with mode
0400. The output must be a distinct, owned, single-link, empty regular file opened
read/write with mode 0600. The caller creates it exclusively and owns its cleanup.
The independent decoder checks the source digest, actual format, dimensions and
bit depth before conversion. Arbitrary paths/options are never passed to the CLI.
Lower resolutions must consume the generated effective master and its verified
digest; the [assembly layer](packaging-assembly.md) enforces that linkage.

The command preserves the historical aspect-preserving `-resize NxN` behavior.
16-bit inputs retain a stored depth of 16; lower depths are encoded at 8 bits.
PNG depth is explicit so constant 16-bit maps cannot silently become 8-bit files.
Successful process exit alone is insufficient: the output is independently fully
decoded, hashed and checked against expected geometry, format and depth. Only
then is it frozen to 0400 and a proof returned. The proof binds source/output
digests, size, image facts, ImageMagick version and policy digest.

## Runtime boundary

Only one conversion runs per worker process; concurrent requests fail with a fixed
BUSY code. Each converter child has an isolated process group, a private cache,
an environment allowlist, disabled core dumps and OS limits (3 GiB address space,
at most 120 CPU seconds, 2 GiB file size, 128 descriptors). The parent enforces a
total deadline, kills/reaps the child group on timeout, suppresses CLI stdout and
stderr, and verifies that the input did not change. Default total time is 150s;
the caller may tighten limits. Large images may legitimately exceed those bounds
and must fail, never be labelled successfully packaged.

The reviewed policy denies all delegates, filters, image modules, coders and
paths, then allows only PNG/JPEG/TIFF/WebP and inherited `fd:` handles. It caps
memory/map cache at 512 MiB each, disk cache at 2 GiB, threads at two and internal
working images at four. ImageMagick counts its own temporary images in that last
limit: a value of one prevents ordinary single-image conversion. The independent
pre/post decoder strictly rejects multiple frames regardless of that limit.
The installed policy must byte-match the packaged policy before every conversion.

Run this component in an unprivileged, network-disabled container with a read-only
root, dropped capabilities, no-new-privileges and bounded private storage. The
policy and OS limits supplement that runtime isolation. They do not protect against
a hostile administrator or another process with the same UID and writable access
to the program/runtime. No source path, raw source content or CLI diagnostics are
returned in errors. [Execution recovery](packaging-execution.md) handles recorded
process-crash orphans under the inherited lease; unknown ownership is preserved.

Policy behavior follows the official [ImageMagick security policy documentation](https://imagemagick.org/security-policy/).
The runtime uses Debian's [Q16-HDRI executable package](https://packages.debian.org/trixie/amd64/imagemagick-7.q16hdri/filelist).
The tested build has ImageMagick `7.1.1-43`, Debian package
`8:7.1.1.43+dfsg1-1+deb13u12`, and policy SHA-256
`4f01e6391b3c53642c6d813fc9f350a4a277f4767385ad4b4ed5a8438a50a0a4`.
Rebuilds obtain distribution security updates; record the built image digest for
each eventual job because a CLI version alone does not identify every library.

## Verification and repeatable command

From the dedicated worktree, with local Linux Docker Desktop running:

```powershell
.\scripts\tests\test-packaging-script.ps1
.\scripts\test-packaging.ps1
```

The first command is an offline runner-safety test. Its 11 cases check collisions,
local context and context changes, build/test failure, required runtime gate,
container isolation and ownership-bound cleanup. The second builds a unique image
from a complete worker snapshot, then runs the entire worker suite as UID 65532
without network, host mounts, databases or ports. It has a read-only root, 4 GiB
memory, two CPUs, 256 PIDs and a 2 GiB tmpfs. The owned container is removed; its
uniquely named image is retained. No existing databases or volumes are touched.

Run this alongside the existing application/E2E suites when reviewing packaging.
The ordinary minimal worker/Windows environment explicitly skips the dedicated
conversion tests when ImageMagick is absent. This runner sets
`REQUIRE_PACKAGING_RUNTIME=1`, making a missing converter fail instead of skip.

On 2026-09-18, run `reawote-packaging-76112782f8c049c3823dea7371808a58` passed
456 worker tests in 62.02s, no skips, with two existing dependency deprecations.
After adding the concurrency guard and strict original bit-depth binding, all
41 conversion cases passed in 31.42s, with no skips or warnings. They include
real output in all four formats, 16-bit grayscale and color PNG/TIFF, exact
rounding and pixel comparison against the archived resize command on synthetic
data, invalid outputs despite exit zero, file-size/time limits, concurrent work,
environment injection, policy drift, forbidden path reads and multiple frames.
No production golden assets or historical installed Windows binary were tested;
the pixel comparison uses the same current runtime for both commands.
