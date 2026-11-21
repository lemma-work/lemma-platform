"""An image file made into what a decision sends a model.

Models read images at a bounded resolution -- about 1568 pixels on the long
edge -- and shrink anything larger on arrival, so a phone photo is several
megabytes of pixels nobody looks at, and over the per-image limit some
providers enforce. Shrinking it here is what lets a receipt photo be judged as
it was taken.

The file's own bytes decide what it is, not the type it was stored with: a
file that does not open as a PNG, JPEG, WebP or GIF is refused rather than
sent to a provider under a label it does not match. An animated image is
refused too, rather than judged on its first frame without saying so.

Pillow decodes, which is CPU work: call this off the event loop.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from app.modules.decisions.domain.request import MAX_IMAGE_BYTES, DecisionImage

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

#: Matches what the providers resize to on their side.
MAX_LONG_EDGE = 1568
#: Checked from the header, before anything is decoded: a small file can
#: declare an enormous canvas.
MAX_PIXELS = 50_000_000
_JPEG_QUALITY = 85
_MEDIA_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}


class ImageRefusedError(Exception):
    """The file cannot be sent as an image; the message says why, for the caller."""


def prepare_image(content: bytes, *, name: str) -> DecisionImage:
    """`content` as it will be sent: unchanged when it already fits, else
    shrunk to `MAX_LONG_EDGE` and re-encoded as JPEG."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(content)) as image:
            media_type = _MEDIA_TYPES.get(image.format or "")
            if media_type is None:
                raise ImageRefusedError("Must be a PNG, JPEG, WebP or GIF image.")
            width, height = image.size
            if width * height > MAX_PIXELS:
                raise ImageRefusedError(f"At most {MAX_PIXELS} pixels.")
            if getattr(image, "n_frames", 1) > 1:
                raise ImageRefusedError("Must be a still image, not an animation.")
            if max(width, height) <= MAX_LONG_EDGE and len(content) <= MAX_IMAGE_BYTES:
                image.load()  # a truncated file fails here, not at the provider
                return DecisionImage(data=content, media_type=media_type, name=name)
            return DecisionImage(
                data=_shrunk(image), media_type="image/jpeg", name=name
            )
    # Everything Pillow raises for a file it will not decode: an unknown or
    # truncated format (`OSError`), a bad parameter, and a decompression bomb.
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageRefusedError("Could not be read as an image.") from exc


def _shrunk(image: PILImage) -> bytes:
    from PIL import Image

    ratio = min(1.0, MAX_LONG_EDGE / max(image.size))
    target = (max(1, round(image.width * ratio)), max(1, round(image.height * ratio)))
    # A JPEG decodes straight at a fraction of its size, which is most of the
    # cost of shrinking a phone photo.
    image.draft("RGB", target)
    frame = _flattened(image)
    if frame.size != target:
        frame = frame.resize(target, Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    frame.save(buffer, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    shrunk = buffer.getvalue()
    if len(shrunk) > MAX_IMAGE_BYTES:
        raise ImageRefusedError(
            f"At most {MAX_IMAGE_BYTES} bytes once shrunk; crop it first."
        )
    return shrunk


def _flattened(image: PILImage) -> PILImage:
    """RGB, with any transparency laid on white rather than black."""
    from PIL import Image

    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")
