"""Sizing an image for a model that is going to downscale it anyway.

Vision models consume images at a bounded resolution — around 1568px on the
long edge — and shrink anything larger on receipt. Sending the original is
therefore paid for twice and read once: tokens and upload time for pixels the
model discards, and, for a remote harness, a base64 payload a third larger
again on a stdio bridge with a fixed response ceiling.

The PDF page renderer already works this way (``pdf_render_max_long_edge``).
This is the same policy for images that arrive as files rather than as rendered
pages, so both routes to a model's eyes cost the same.
"""

from __future__ import annotations

import io

from PIL import Image

from app.core.log.log import get_logger

logger = get_logger(__name__)

# Matches ``datastore_settings.pdf_render_max_long_edge``. Deliberately a
# constant rather than an import: agent tools do not reach into another
# module's internals, and the number is a property of the models, not of PDFs.
VISION_MAX_LONG_EDGE = 1568
VISION_JPEG_QUALITY = 80

# Formats where re-encoding loses something the model may need: an animation, or
# a vector that has no pixels to resize in the first place.
_LEAVE_ALONE = {"image/gif", "image/svg+xml"}


def downscale_for_vision(content: bytes, media_type: str) -> tuple[bytes, str]:
    """Shrink an image to what a model will actually look at.

    Returns the original bytes and media type unchanged whenever shrinking is
    not obviously right — an unreadable file, an animation, a vector, or an
    image already small enough. Never raises: a tool that cannot resize an
    image should still show it.
    """
    if media_type in _LEAVE_ALONE:
        return content, media_type
    try:
        with Image.open(io.BytesIO(content)) as image:
            if getattr(image, "n_frames", 1) > 1:
                return content, media_type
            if max(image.size) <= VISION_MAX_LONG_EDGE:
                return content, media_type
            ratio = VISION_MAX_LONG_EDGE / max(image.size)
            resized = image.resize(
                (
                    max(1, round(image.width * ratio)),
                    max(1, round(image.height * ratio)),
                ),
                Image.LANCZOS,
            )
            # JPEG has no alpha; flatten onto white so transparency reads
            # cleanly rather than turning black.
            if resized.mode not in ("RGB", "L"):
                resized = resized.convert("RGB")
            buffer = io.BytesIO()
            resized.save(
                buffer, format="JPEG", quality=VISION_JPEG_QUALITY, optimize=True
            )
    # Everything Pillow raises for an image it will not decode: a format it does
    # not recognise or a truncated file (both `OSError`), a bad parameter, and a
    # decompression bomb. Showing the image beats resizing it, so none of them
    # is a reason to fail the tool.
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        logger.debug(
            "agent.tools.image_payload.downscale_skipped.diagnostic",
            error_type=type(exc).__name__,
        )
        return content, media_type

    shrunk = buffer.getvalue()
    # A re-encode that made things worse is not worth having.
    if len(shrunk) >= len(content):
        return content, media_type
    return shrunk, "image/jpeg"
