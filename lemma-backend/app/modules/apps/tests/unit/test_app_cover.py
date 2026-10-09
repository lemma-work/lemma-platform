"""The cover the host draws for an app whose build ships none."""

from __future__ import annotations

import io

from PIL import Image

from app.modules.apps.services.app_cover import (
    COVER_HEIGHT,
    COVER_WIDTH,
    AppCoverSpec,
    cover_etag,
    render_app_cover,
)
from app.modules.apps.services.app_icon import icon_ink


def spec(
    name: str = "Deals",
    description: str | None = "Every open deal, who owns it, and what happens next.",
    slug: str = "deals",
) -> AppCoverSpec:
    return AppCoverSpec(
        name=name, description=description, slug=slug, label=f"{slug}.lemma.work"
    )


def picture(cover: AppCoverSpec) -> Image.Image:
    return Image.open(io.BytesIO(render_app_cover(cover))).convert("RGB")


def test_cover_is_the_size_link_unfurls_ask_for():
    assert picture(spec()).size == (COVER_WIDTH, COVER_HEIGHT)


def test_cover_carries_the_icons_colour_at_its_right_edge():
    # The letter is the home-screen icon's, cut off by the right edge, so the
    # cover and the icon read as one app.
    image = picture(spec())
    edge = {image.getpixel((COVER_WIDTH - 1, y)) for y in range(COVER_HEIGHT)}

    assert icon_ink("deals") in edge


def test_a_name_the_face_cannot_draw_is_printed_as_its_slug():
    # Pillow's face has no CJK, and closing up the gaps in a half-drawn name
    # would say something else, so the slug stands in -- the icon's own rule.
    japanese = picture(
        spec(
            name="請求書トラッカー",
            description="請求書を追跡します",
            slug="invoice-tracker",
        )
    )
    slug_words = picture(
        spec(name="Invoice tracker", description=None, slug="invoice-tracker")
    )

    assert japanese.tobytes() == slug_words.tobytes()


def test_a_symbol_the_face_lacks_is_dropped_from_a_name():
    with_emoji = picture(spec(name="Weekly 🚀 Wins", slug="weekly-wins"))
    without = picture(spec(name="Weekly Wins", slug="weekly-wins"))

    assert with_emoji.tobytes() == without.tobytes()


def test_a_name_too_long_for_two_lines_still_draws():
    long_name = "Quarterly revenue dashboard for the leadership team " * 4

    assert picture(spec(name=long_name)).size == (COVER_WIDTH, COVER_HEIGHT)


def test_etag_moves_with_anything_the_cover_shows():
    base = cover_etag(spec())

    assert cover_etag(spec()) == base
    assert cover_etag(spec(name="Pipeline")) != base
    assert cover_etag(spec(description="Something else")) != base
    assert cover_etag(spec(slug="deals-two")) != base
