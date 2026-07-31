from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from app.modules.icon.services.raster_validation import (
    _container_ends_at_eof,
    validate_raster_icon,
)


def _image_bytes(image_format: str, *, size: tuple[int, int] = (2, 2)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color=(20, 40, 60)).save(output, format=image_format)
    return output.getvalue()


@pytest.mark.parametrize(
    ("image_format", "media_type"),
    [
        ("PNG", "image/png"),
        ("JPEG", "image/jpeg"),
        ("GIF", "image/gif"),
        ("WEBP", "image/webp"),
        ("BMP", "image/bmp"),
    ],
)
def test_container_validation_accepts_complete_supported_rasters(
    image_format: str, media_type: str
) -> None:
    assert _container_ends_at_eof(_image_bytes(image_format), media_type) is True


@pytest.mark.parametrize(
    ("data", "media_type"),
    [
        (_image_bytes("PNG") + b"trailing", "image/png"),
        (_image_bytes("JPEG")[:-2], "image/jpeg"),
        (_image_bytes("GIF")[:-1], "image/gif"),
        (_image_bytes("WEBP") + b"x", "image/webp"),
        (_image_bytes("BMP") + b"x", "image/bmp"),
        (b"anything", "image/tiff"),
    ],
)
def test_container_validation_rejects_truncated_trailing_and_unknown_data(
    data: bytes, media_type: str
) -> None:
    assert _container_ends_at_eof(data, media_type) is False


def test_validate_raster_icon_verifies_type_and_dimensions(monkeypatch) -> None:
    png = _image_bytes("PNG", size=(3, 2))
    result = validate_raster_icon(
        png,
        detected_media_type="image/png",
        max_dimension=10,
        max_pixels=100,
    )
    assert (result.media_type, result.width, result.height) == ("image/png", 3, 2)

    monkeypatch.setattr(
        "app.modules.icon.services.raster_validation._container_ends_at_eof",
        lambda data, media_type: True,
    )
    with pytest.raises(ValueError, match="does not match"):
        validate_raster_icon(
            png,
            detected_media_type="image/gif",
            max_dimension=10,
            max_pixels=100,
        )

    with pytest.raises(ValueError, match="configured limit"):
        validate_raster_icon(
            png,
            detected_media_type="image/png",
            max_dimension=2,
            max_pixels=100,
        )


def test_validate_raster_icon_maps_decode_failures() -> None:
    structurally_terminated_but_invalid_png = (
        b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00IEND" + b"\x00\x00\x00\x00"
    )
    with pytest.raises(ValueError, match="could not be decoded"):
        validate_raster_icon(
            structurally_terminated_but_invalid_png,
            detected_media_type="image/png",
            max_dimension=10,
            max_pixels=100,
        )
