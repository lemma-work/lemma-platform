"""Locate a usable TrueType face for server-rendered images.

Two things render text into PNGs -- the published pod's social card and a
hosted app's home-screen icon -- and neither ships a font. They read the
container's, which is Debian's DejaVu with Liberation as the fallback, and drop
to Pillow's bitmap default only when an image has no system fonts at all (a
bare test environment). One copy, because a second copy is a second list of
paths to forget to update when the base image changes.
"""

from __future__ import annotations

from PIL import ImageFont

_CANDIDATES: dict[bool, tuple[str, ...]] = {
    True: (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ),
    False: (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ),
}


def load_font(size: int, *, bold: bool = False):
    """Return a face at ``size``, falling back to Pillow's built-in default."""
    for candidate in _CANDIDATES[bold]:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)
