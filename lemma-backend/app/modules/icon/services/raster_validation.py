"""Strict raster validation for same-origin public icons."""

from __future__ import annotations

import struct
import warnings
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, UnidentifiedImageError


_FORMAT_MEDIA_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
}


@dataclass(frozen=True, slots=True)
class RasterInfo:
    media_type: str
    width: int
    height: int


def _container_ends_at_eof(data: bytes, media_type: str) -> bool:
    if media_type == "image/png":
        position = 8
        while position + 12 <= len(data):
            length = struct.unpack(">I", data[position : position + 4])[0]
            chunk_type = data[position + 4 : position + 8]
            position += length + 12
            if position > len(data):
                return False
            if chunk_type == b"IEND":
                return position == len(data)
        return False
    if media_type == "image/jpeg":
        return len(data) >= 4 and data.endswith(b"\xff\xd9")
    if media_type == "image/gif":
        return data.endswith(b";")
    if media_type == "image/webp":
        return (
            len(data) >= 12
            and data[:4] == b"RIFF"
            and data[8:12] == b"WEBP"
            and int.from_bytes(data[4:8], "little") + 8 == len(data)
        )
    if media_type == "image/bmp":
        return len(data) >= 6 and int.from_bytes(data[2:6], "little") == len(data)
    return False


def validate_raster_icon(
    data: bytes,
    *,
    detected_media_type: str,
    max_dimension: int,
    max_pixels: int,
) -> RasterInfo:
    """Verify decode, detected format, dimensions, and absence of trailing data."""
    if not _container_ends_at_eof(data, detected_media_type):
        raise ValueError("Raster container is malformed or has trailing data")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                media_type = _FORMAT_MEDIA_TYPES.get(str(image.format).upper())
                width, height = image.size
                image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("Raster image could not be decoded") from exc
    if media_type != detected_media_type:
        raise ValueError("Detected raster type does not match decoded image")
    if width <= 0 or height <= 0:
        raise ValueError("Raster dimensions must be positive")
    if width > max_dimension or height > max_dimension or width * height > max_pixels:
        raise ValueError("Raster dimensions exceed the configured limit")
    return RasterInfo(media_type=media_type, width=width, height=height)
