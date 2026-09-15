"""Decode one inherited read-only image descriptor in a resource-limited child.

No filename, source contents, exception message or host environment is returned.
"""
import hashlib
import json
import os
import sys

MAX_PIXELS = 16_384**2
MAX_SIDE = 32_768


def inspect(fd: int) -> dict:
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    with os.fdopen(os.dup(fd), "rb") as stream:
        before = os.fstat(stream.fileno())
        digest = hashlib.sha256()
        header = stream.read(32)
        digest.update(header)
        while chunk := stream.read(1024 * 1024): digest.update(chunk)
        stream.seek(0)
        formats = ("PNG", "JPEG", "TIFF", "WEBP")
        with Image.open(stream, formats=formats) as image:
            width, height = image.size
            if (width > MAX_SIDE or height > MAX_SIDE or width * height > MAX_PIXELS
                    or width <= 0 or height <= 0):
                return {"error": "IMAGE_DIMENSION_LIMIT"}
            if getattr(image, "n_frames", 1) != 1:
                return {"error": "IMAGE_MULTIFRAME_UNSUPPORTED"}
            image_format = image.format
            bits = 8
            if image_format == "PNG":
                if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
                    return {"error": "IMAGE_UNREADABLE"}
                bits = header[24]
            elif image_format == "TIFF":
                samples = image.tag_v2.get(258, (8,))
                if isinstance(samples, int): samples = (samples,)
                if not samples or len(set(samples)) != 1:
                    return {"error": "IMAGE_BIT_DEPTH_UNSUPPORTED"}
                bits = samples[0]
            if bits not in {1, 2, 4, 8, 16}:
                return {"error": "IMAGE_BIT_DEPTH_UNSUPPORTED"}
            if image.mode not in {"1", "L", "LA", "P", "RGB", "RGBA", "I;16", "I;16B", "I;16L"}:
                return {"error": "IMAGE_MODE_UNSUPPORTED"}
            image.verify()
        stream.seek(0)
        with Image.open(stream, formats=formats) as image:
            image.load()  # Header identification alone is not a readability check.
        after = os.fstat(stream.fileno())
        def signature(info):
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if signature(before) != signature(after):
            return {"error": "IMAGE_SOURCE_CHANGED"}
        return {"width": width, "height": height, "bits": int(bits), "format": image_format,
                "sha256": digest.hexdigest()}


def main() -> int:
    try:
        if len(sys.argv) != 2 or not sys.argv[1].isdigit() or int(sys.argv[1]) < 3:
            raise ValueError()
        result = inspect(int(sys.argv[1]))
    except ImportError:
        result = {"error": "IMAGE_PROBE_UNAVAILABLE"}
    except MemoryError:
        result = {"error": "IMAGE_RESOURCE_LIMIT"}
    except Exception:
        result = {"error": "IMAGE_UNREADABLE"}
    print(json.dumps(result, separators=(",", ":")))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
