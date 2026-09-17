"""Render a bounded, metadata-free JPEG from one inherited read-only descriptor."""
import base64
import hashlib
import io
import json
import os
import sys

MAX_SOURCE_BYTES = 64 * 1024**2
MAX_PIXELS = 32 * 1024**2
MAX_OUTPUT_BYTES = 2 * 1024**2
MAX_OUTPUT_SIDE = 1024
FORMATS = ("JPEG", "PNG", "TIFF", "WEBP")


def decode(fd: int) -> dict:
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 768 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    def signature(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)
    with os.fdopen(os.dup(fd), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not 0 < before.st_size <= MAX_SOURCE_BYTES:
            return {"error": "PREVIEW_FILE_LIMIT"}
        digest = hashlib.sha256(); count = 0
        while chunk := stream.read(1024 * 1024):
            count += len(chunk)
            if count > MAX_SOURCE_BYTES: return {"error": "PREVIEW_FILE_LIMIT"}
            digest.update(chunk)
        if count != before.st_size: return {"error": "PREVIEW_SOURCE_CHANGED"}
        stream.seek(0)
        with Image.open(stream, formats=FORMATS) as source:
            width, height = source.size
            if width <= 0 or height <= 0 or max(width, height) > 32768 or width * height > MAX_PIXELS:
                return {"error": "PREVIEW_PIXEL_LIMIT"}
            if getattr(source, "n_frames", 1) != 1:
                return {"error": "PREVIEW_MULTIFRAME_UNSUPPORTED"}
            if source.mode not in {"1", "L", "LA", "P", "RGB", "RGBA", "I;16", "I;16B", "I;16L"}:
                return {"error": "PREVIEW_MODE_UNSUPPORTED"}
            source_format = source.format
            source.verify()
        stream.seek(0)
        with Image.open(stream, formats=FORMATS) as source:
            source.load()
            oriented = ImageOps.exif_transpose(source)
            if oriented.mode in {"I;16", "I;16B", "I;16L"}:
                # Preserve the full unsigned grayscale range; a direct RGB
                # conversion clips every sample above 255 to white.
                oriented = oriented.convert("I").point(lambda sample: sample / 257).convert("L")
            # A fresh image prevents source comments/EXIF/ICC and other ancillary
            # fields from being inherited by the encoder.
            clean = Image.new("RGB", oriented.size, (255, 255, 255))
            rgba = oriented.convert("RGBA")
            clean.paste(rgba, mask=rgba.getchannel("A"))
            clean.thumbnail((MAX_OUTPUT_SIDE, MAX_OUTPUT_SIDE), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            clean.save(output, format="JPEG", quality=85, optimize=False)
            data = output.getvalue()
            if len(data) > MAX_OUTPUT_BYTES: return {"error": "PREVIEW_OUTPUT_LIMIT"}
            if signature(before) != signature(os.fstat(stream.fileno())):
                return {"error": "PREVIEW_SOURCE_CHANGED"}
            return {"source_sha256": digest.hexdigest(), "source_format": source_format,
                "width": clean.width, "height": clean.height, "media_type": "image/jpeg",
                "sha256": hashlib.sha256(data).hexdigest(), "data": base64.b64encode(data).decode("ascii")}


def main():
    try:
        if len(sys.argv) != 2 or not sys.argv[1].isdigit() or int(sys.argv[1]) < 3: raise ValueError()
        result = decode(int(sys.argv[1]))
    except ImportError:
        result = {"error": "PREVIEW_DECODER_UNAVAILABLE"}
    except MemoryError:
        result = {"error": "PREVIEW_RESOURCE_LIMIT"}
    except Exception:
        result = {"error": "PREVIEW_UNREADABLE"}
    print(json.dumps(result, separators=(",", ":")))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
