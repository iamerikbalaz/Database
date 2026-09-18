"""Apply OS limits before replacing this child with the fixed image executable."""
import math
import os
import resource
import sys

EXECUTABLE = "/usr/bin/magick-im7.q16hdri"


def main():
    # Arguments are constructed exclusively by packaging_convert, not an API.
    source, target, cache, side, bits, seconds, size = map(int, sys.argv[1:8])
    image_format = sys.argv[8]
    if (len(sys.argv) != 9 or min(source, target, cache) < 3 or len({source, target, cache}) != 3
            or not 1 <= side <= 32768 or bits not in {8, 16} or not 1 <= seconds <= 120
            or not 0 < size <= 2 * 1024**3 or image_format not in {"PNG", "JPEG", "TIFF", "WEBP"}):
        return 1
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3, 3 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (math.ceil(seconds), math.ceil(seconds)))
    resource.setrlimit(resource.RLIMIT_FSIZE, (size, size))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    os.umask(0o077)
    os.fchdir(cache)
    os.close(cache)
    args = [EXECUTABLE, f"{image_format}:fd:{source}", "-resize", f"{side}x{side}", "-depth", str(bits)]
    # PNG otherwise optimizes constant or low-variation 16-bit data to 8 bits.
    if image_format == "PNG": args.extend(["-define", f"png:bit-depth={bits}"])
    args.append(f"{image_format}:fd:{target}")
    os.execve(EXECUTABLE, args, dict(os.environ))


if __name__ == "__main__":
    try: raise SystemExit(main())
    except Exception: raise SystemExit(1) from None
