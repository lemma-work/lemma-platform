"""Generate the durable social card committed with a published pod."""

from __future__ import annotations

import io
import textwrap

from PIL import Image, ImageDraw

from app.core.fonts import load_font

WIDTH = 1200
HEIGHT = 630

_INK = "#11110F"
_MUTED = "#595851"
_PAPER = "#F3F1EA"
_PANEL = "#E9E6DC"
_CARD = "#F8F7F2"


def _clean(value: str | None, fallback: str, limit: int) -> str:
    normalized = " ".join((value or "").split()) or fallback
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1].rstrip()}…"


def render_social_card(*, pod_name: str, source_label: str) -> bytes:
    """Return a 1200×630 PNG with public, bounded pod copy only."""
    name = _clean(pod_name, "A Lemma pod", 52)
    label = _clean(source_label, "lemma.work", 100)

    image = Image.new("RGB", (WIDTH, HEIGHT), _PAPER)
    draw = ImageDraw.Draw(image)
    draw.rectangle((790, 0, WIDTH, HEIGHT), fill=_PANEL)

    # Lemma mark + wordmark.
    draw.rounded_rectangle((64, 76, 74, 90), radius=2, fill=_INK)
    draw.rounded_rectangle((79, 66, 89, 90), radius=2, fill=_INK)
    draw.rounded_rectangle((94, 56, 104, 90), radius=2, fill=_INK)
    draw.text((120, 58), "Lemma", font=load_font(27, bold=True), fill=_INK)

    draw.text(
        (64, 166),
        "RUN IT ON LEMMA",
        font=load_font(19, bold=True),
        fill=_MUTED,
        spacing=3,
    )
    title_lines = textwrap.wrap(name, width=24, max_lines=2, placeholder="…") or [name]
    title_font = load_font(74 if max(map(len, title_lines)) <= 22 else 64, bold=True)
    draw.multiline_text(
        (60, 232),
        "\n".join(title_lines),
        font=title_font,
        fill=_INK,
        spacing=4,
    )

    draw.line((64, 526, 742, 526), fill="#C5C1B6", width=2)
    draw.text(
        (64, 544),
        "Apps · agents · workflows · data",
        font=load_font(18),
        fill=_MUTED,
    )
    draw.text((64, 579), label, font=load_font(15), fill=_INK)

    # A compact app/workflow motif.
    draw.rounded_rectangle(
        (846, 96, 1124, 534),
        radius=28,
        fill=_CARD,
        outline="#C9C5BA",
        width=2,
    )
    draw.rounded_rectangle((878, 130, 990, 144), radius=7, fill=_INK)
    draw.rounded_rectangle((878, 156, 1052, 164), radius=4, fill="#BBB7AC")
    for top, background, outline, dot, line in (
        (202, "#E8F0D9", "#B7C49D", "#66833E", "#587137"),
        (310, "#E4EBF2", "#B3C1D0", "#4A6580", "#405B74"),
    ):
        draw.rounded_rectangle(
            (878, top, 1092, top + 84),
            radius=16,
            fill=background,
            outline=outline,
        )
        draw.ellipse((900, top + 22, 920, top + 42), fill=dot)
        draw.rounded_rectangle((932, top + 22, 1058, top + 32), radius=5, fill=line)
        draw.rounded_rectangle((932, top + 43, 1024, top + 50), radius=4, fill=line)
    draw.rounded_rectangle((878, 418, 1092, 492), radius=16, fill=_INK)
    draw.rounded_rectangle((902, 443, 1006, 453), radius=5, fill=_CARD)
    draw.ellipse((1052, 442, 1078, 468), fill=_PAPER)
    draw.line((1059, 455, 1070, 455), fill=_INK, width=2)
    draw.line((1066, 451, 1070, 455), fill=_INK, width=2)
    draw.line((1066, 459, 1070, 455), fill=_INK, width=2)

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
