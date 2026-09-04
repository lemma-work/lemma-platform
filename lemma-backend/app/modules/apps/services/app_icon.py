"""Render a home-screen icon for a hosted app.

An app installed to a phone's home screen is a square next to Instagram, and
an app with no icon is a grey tile with a cropped title -- worse than not
offering the install at all. Apps carry no uploaded icon, so one is derived
from what every app already has: its name and its public slug.

The design is the branding badge's, at icon scale. Near-black plate, one
letter in a colour the slug picks out of a twelve-step ring around the brand
violet. Deterministic, so an app's icon never changes under the person who
installed it, and distinct enough that a home screen of pod apps is not twelve
copies of the same square.

Full-bleed and declared ``any maskable``: Android applies its own mask, so the
glyph is kept inside the central safe circle rather than being drawn onto a
rounded rectangle this code would have to guess the radius of.
"""

from __future__ import annotations

import colorsys
import io
from functools import lru_cache

from PIL import Image, ImageDraw

from app.core.fonts import load_font

# The badge's plate, so a home-screen icon and the "Remix on Lemma" pill are
# recognisably the same product. (``runtime_config._public_app_branding_script``)
_PLATE = (20, 20, 19)

# The brand violet #8b7af5 is hsl(248, 86%, 72%); the ring starts there and
# steps every 30 degrees. Saturation and lightness are held slightly below the
# violet's so every step stays legible on the plate instead of glowing.
_BASE_HUE_DEGREES = 248
_RING_STEPS = 12
_SATURATION = 0.72
_LIGHTNESS = 0.68

# Fraction of the icon's width the glyph is allowed to occupy. A maskable icon
# must keep its content inside the central 80% circle; a box this size is well
# within it at every aspect the letter can take.
_GLYPH_EXTENT = 0.44

# What the manifest, the apple-touch link and the favicon ask for.
ICON_SIZES = (32, 180, 192, 512)


def _initial(name: str, slug: str) -> str:
    """One uppercase ASCII letter or digit for the plate.

    ASCII only, and the slug is the fallback rather than the name's own first
    character: the container's DejaVu has no CJK, so a Japanese app name would
    render a tofu box. A slug is ``[a-z0-9-]`` by the app-host regex, so it
    always has something to give.
    """
    for source in (name, slug):
        for character in source or "":
            if character.isascii() and character.isalnum():
                return character.upper()
    return "L"


def _ink(slug: str) -> tuple[int, int, int]:
    """The slug's step on the ring, as RGB."""
    digest = 0
    for character in slug or "":
        digest = (digest * 31 + ord(character)) & 0xFFFFFFFF
    hue = (_BASE_HUE_DEGREES + (digest % _RING_STEPS) * (360 // _RING_STEPS)) % 360
    red, green, blue = colorsys.hls_to_rgb(hue / 360, _LIGHTNESS, _SATURATION)
    return (round(red * 255), round(green * 255), round(blue * 255))


@lru_cache(maxsize=512)
def _render(letter: str, ink: tuple[int, int, int], size: int) -> bytes:
    image = Image.new("RGB", (size, size), _PLATE)
    draw = ImageDraw.Draw(image)

    # Grow the face until the glyph fills its allowance, then centre on the
    # measured ink rather than on the font's line box -- a capital letter sits
    # above the baseline, so line-box centring hangs it visibly high.
    extent = size * _GLYPH_EXTENT
    font = load_font(max(1, round(size * 0.5)), bold=True)
    left, top, right, bottom = draw.textbbox((0, 0), letter, font=font)
    width, height = right - left, bottom - top
    if width > 0 and height > 0:
        scale = extent / max(width, height)
        font = load_font(max(1, round(size * 0.5 * scale)), bold=True)
        left, top, right, bottom = draw.textbbox((0, 0), letter, font=font)
        width, height = right - left, bottom - top

    draw.text(
        ((size - width) / 2 - left, (size - height) / 2 - top),
        letter,
        font=font,
        fill=ink,
    )

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_app_icon(*, name: str, slug: str, size: int) -> bytes:
    """Return the app's icon as a square PNG of ``size`` pixels."""
    return _render(_initial(name, slug), _ink(slug), size)
