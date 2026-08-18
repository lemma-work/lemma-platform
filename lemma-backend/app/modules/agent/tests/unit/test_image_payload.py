"""Sizing an image for the model that is going to downscale it anyway."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.modules.agent.tools.image_payload import (
    VISION_MAX_LONG_EDGE,
    downscale_for_vision,
)


def _png(width: int, height: int, mode: str = "RGB") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, (width, height), (200, 40, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_a_large_photo_is_shrunk_to_what_the_model_reads() -> None:
    """The saving is the point: pixels past the model's own ceiling are paid
    for in tokens on the way in and discarded on arrival."""
    original = _png(4000, 3000)

    payload, media_type = downscale_for_vision(original, "image/png")

    assert media_type == "image/jpeg"
    assert len(payload) < len(original)
    with Image.open(io.BytesIO(payload)) as shrunk:
        assert max(shrunk.size) == VISION_MAX_LONG_EDGE
        assert shrunk.width / shrunk.height == pytest.approx(4 / 3, rel=0.01)


def test_an_image_already_small_enough_is_left_alone() -> None:
    original = _png(800, 600)

    payload, media_type = downscale_for_vision(original, "image/png")

    assert payload == original
    assert media_type == "image/png"


def test_transparency_is_flattened_rather_than_blackened() -> None:
    """JPEG has no alpha, so an un-flattened RGBA turns its transparent
    regions black — which changes what the model sees."""
    original = _png(3000, 3000, mode="RGBA")

    payload, media_type = downscale_for_vision(original, "image/png")

    assert media_type == "image/jpeg"
    with Image.open(io.BytesIO(payload)) as shrunk:
        assert shrunk.mode == "RGB"


def test_an_animation_keeps_its_frames() -> None:
    frames = [Image.new("P", (2000, 2000), 0), Image.new("P", (2000, 2000), 1)]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:])
    original = buffer.getvalue()

    assert downscale_for_vision(original, "image/gif") == (original, "image/gif")


def test_something_unreadable_is_shown_rather_than_dropped() -> None:
    """A tool that cannot resize an image should still show it."""
    assert downscale_for_vision(b"not an image", "image/png") == (
        b"not an image",
        "image/png",
    )
