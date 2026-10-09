"""Draw the cover a hosted app's links unfurl with, when its build ships none.

A link to an app pasted into Slack, iMessage or X shows one large picture above
the title. An app's own cover -- its first screen, rendered by the agent that
built it, with sample rows and no sign-in -- lives in its build at
``/.lemma/cover.png`` and is served from there. This module draws the picture
for every app that has not got one, from what every app already has: its name,
its description and its address.

Nothing here reads the app's data, and the host never photographs a running
app. A cover leaves Lemma for good -- the services that unfurl a link keep
their own copy -- and whoever's view were photographed, everyone the link
reached would see rows only that person may open.

The design is the home-screen icon blown up: the same near-black plate, the
same letter in the colour the slug picks, so an app's cover and its icon read
as one thing. The letter is cropped off the right edge, and the name and
description sit in the room it leaves on the left.
"""

from __future__ import annotations

import hashlib
import io
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from app.core import app_install
from app.core.fonts import load_font
from app.modules.apps.services.app_icon import icon_ink, icon_letter

COVER_WIDTH = 1200
COVER_HEIGHT = 630

# As the resolver sees it: normalised asset paths carry no leading slash.
COVER_ASSET_PATH = app_install.COVER_PATH.lstrip("/")

# Part of every drawn cover's ETag. Bump it when the drawing changes, or the
# covers already cached keep revalidating against the old picture.
_DESIGN_VERSION = "1"

_PLATE = app_install.PLATE_COLOR
_VIOLET = "#8b7af5"
_TITLE_INK = "#ffffff"
# White at 62% and 45% over the plate, flattened, because Pillow draws opaque
# text onto an RGB image.
_DETAIL_INK = "#a6a6a5"
_LABEL_INK = "#7e7e7d"

_MARGIN = 64
_TEXT_WIDTH = 560
_TITLE_TOP = 176
# Largest first; the first at which the whole name fits two lines is used.
_TITLE_SIZES = (88, 76, 64, 56)
_DETAIL_SIZE = 30
_DETAIL_MAX_LINES = 3
_LABEL_SIZE = 22

# The letter's ink is this much taller than the cover, so both edges cut it.
_LETTER_HEIGHT = 1.12
# How much of its width shows before the right edge cuts it...
_LETTER_SHOWN = 0.88
# ...and the most that may show, which keeps it clear of the text column.
_LETTER_VISIBLE_WIDTH = COVER_WIDTH - _MARGIN - _TEXT_WIDTH - 16
_LETTER_PROBE_SIZE = 200

type _Font = ImageFont.FreeTypeFont | ImageFont.ImageFont


@dataclass(frozen=True, slots=True)
class AppCoverSpec:
    """Everything a drawn cover shows. All of it is the app's public identity."""

    name: str
    description: str | None
    slug: str
    # The address the link points at, without its scheme.
    label: str


def cover_etag(spec: AppCoverSpec) -> str:
    """A short hash over what the drawn cover shows and how it is drawn."""
    parts = (_DESIGN_VERSION, spec.name, spec.description or "", spec.slug, spec.label)
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:12]


def render_app_cover(spec: AppCoverSpec) -> bytes:
    """Return the drawn cover as a ``COVER_WIDTH`` x ``COVER_HEIGHT`` PNG."""
    return _render(spec)


def _drawable(character: str) -> bool:
    """Whether the container's DejaVu draws ``character`` correctly, unshaped.

    Latin, Greek and Cyrillic with their punctuation. Pillow without raqm does
    no shaping, so Arabic or Devanagari would come out disjointed even where
    the face has the glyphs, and CJK is not in the face at all.
    """
    code = ord(character)
    return code < 0x0530 or 0x2000 <= code <= 0x20CF


def _for_drawing(text: str | None) -> str | None:
    """``text`` as the cover can draw it, or None when it cannot.

    A symbol the face lacks -- an emoji in a name -- is dropped. A letter it
    lacks means the text is in a script the cover cannot draw, and printing
    what is left with the gaps closed would say something else, so the whole
    text is refused instead.
    """
    kept: list[str] = []
    for character in text or "":
        category = unicodedata.category(character)
        if category.startswith("C"):
            continue
        if _drawable(character):
            kept.append(character)
        elif category.startswith("L"):
            return None
    return " ".join("".join(kept).split()) or None


def _slug_title(slug: str) -> str:
    """A name to print when the app's own cannot be: its slug, as words.

    A slug is ``[a-z0-9-]`` by the app-host rule, so this always draws.
    """
    words = " ".join(part for part in (slug or "").split("-") if part)
    return words[:1].upper() + words[1:] if words else "Lemma app"


def _ellipsize(line: str, font: _Font, width: int) -> str:
    """``line`` cut to end in an ellipsis within ``width``, at a word if it can."""
    if font.getlength(f"{line}…") <= width:
        return f"{line}…"
    words = line.split(" ")
    while len(words) > 1:
        words.pop()
        shorter = " ".join(words).rstrip(" ,;:-")
        if font.getlength(f"{shorter}…") <= width:
            return f"{shorter}…"
    while line and font.getlength(f"{line}…") > width:
        line = line[:-1]
    return f"{line.rstrip()}…"


def _wrap(text: str, font: _Font, width: int, max_lines: int) -> list[str]:
    """Break ``text`` into at most ``max_lines`` lines no wider than ``width``.

    By words; a word wider than the column on its own is cut, and the last line
    ends in an ellipsis when the text runs out of room.
    """
    words = text.split()
    lines: list[str] = []
    while words and len(lines) < max_lines:
        line = words.pop(0)
        while words and font.getlength(f"{line} {words[0]}") <= width:
            line = f"{line} {words.pop(0)}"
        if font.getlength(line) > width:
            line = _ellipsize(line, font, width)
        lines.append(line)
    if words and lines and not lines[-1].endswith("…"):
        lines[-1] = _ellipsize(lines[-1], font, width)
    return lines


def _title_lines(title: str) -> tuple[int, _Font, list[str]]:
    """The largest size at which the whole name fits in two lines.

    A name that fits at none of them is set at the smallest and cut short.
    """
    for size in _TITLE_SIZES[:-1]:
        font = load_font(size)
        lines = _wrap(title, font, _TEXT_WIDTH, 2)
        if " ".join(lines) == title:
            return size, font, lines
    size = _TITLE_SIZES[-1]
    font = load_font(size)
    return size, font, _wrap(title, font, _TEXT_WIDTH, 2)


def _glyph(letter: str, size: int) -> Image.Image | None:
    """``letter`` at ``size`` as a mask cropped to its ink.

    Measured from pixels, because a text box includes the face's side bearings
    and line spacing, and a letter placed by those sits visibly off the edge it
    was meant to touch.
    """
    canvas = Image.new("L", (size * 2, size * 2), 0)
    ImageDraw.Draw(canvas).text(
        (size // 2, size // 2), letter, font=load_font(size, bold=True), fill=255
    )
    box = canvas.getbbox()
    return canvas.crop(box) if box else None


def _draw_letter(image: Image.Image, letter: str, ink: tuple[int, int, int]) -> None:
    """The icon's letter, taller than the cover and cut off at its right edge.

    It reads as a shape bigger than the frame rather than a glyph placed in it.
    However wide the letter is, no more than ``_LETTER_VISIBLE_WIDTH`` of it
    shows, so a W is cut harder than an H instead of reaching under the name.
    """
    probe = _glyph(letter, _LETTER_PROBE_SIZE)
    if probe is None:
        return
    size = round(_LETTER_PROBE_SIZE * COVER_HEIGHT * _LETTER_HEIGHT / probe.height)
    glyph = _glyph(letter, size)
    if glyph is None:
        return
    visible = min(round(glyph.width * _LETTER_SHOWN), _LETTER_VISIBLE_WIDTH)
    image.paste(
        ink,
        (COVER_WIDTH - visible, (COVER_HEIGHT - glyph.height) // 2),
        mask=glyph,
    )


def _draw_mark(draw: ImageDraw.ImageDraw) -> None:
    """Lemma's three rising bars, as the badge and the offline page draw them."""
    baseline = 92
    for index, height in enumerate((15, 24, 36)):
        left = _MARGIN + index * 14
        draw.rounded_rectangle(
            (left, baseline - height, left + 8, baseline), radius=4, fill=_VIOLET
        )


@lru_cache(maxsize=128)
def _render(spec: AppCoverSpec) -> bytes:
    image = Image.new("RGB", (COVER_WIDTH, COVER_HEIGHT), _PLATE)
    _draw_letter(image, icon_letter(spec.name, spec.slug), icon_ink(spec.slug))
    draw = ImageDraw.Draw(image)
    _draw_mark(draw)

    title = _for_drawing(spec.name) or _slug_title(spec.slug)
    title_size, title_font, title_lines = _title_lines(title)
    top = _TITLE_TOP
    for line in title_lines:
        draw.text((_MARGIN, top), line, font=title_font, fill=_TITLE_INK)
        top += round(title_size * 1.08)

    label_top = COVER_HEIGHT - _MARGIN - _LABEL_SIZE
    detail = _for_drawing(spec.description)
    if detail:
        detail_font = load_font(_DETAIL_SIZE)
        step = round(_DETAIL_SIZE * 1.4)
        top += 28
        room = max(0, (label_top - 32 - top) // step)
        for line in _wrap(
            detail, detail_font, _TEXT_WIDTH, min(_DETAIL_MAX_LINES, room)
        ):
            draw.text((_MARGIN, top), line, font=detail_font, fill=_DETAIL_INK)
            top += step

    label = _for_drawing(spec.label)
    if label:
        label_font = load_font(_LABEL_SIZE)
        for line in _wrap(label, label_font, _TEXT_WIDTH, 1):
            draw.text((_MARGIN, label_top), line, font=label_font, fill=_LABEL_INK)

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
