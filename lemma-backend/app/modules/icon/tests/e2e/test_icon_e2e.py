"""Upload and serve an icon through the real routes.

`mod:icon` had no e2e directory at all. Its unit tests cover `IconService` and
`validate_raster_icon` directly, which left the parts that only exist as HTTP
behaviour untested: the magic-byte sniff that rejects SVG, the `nosniff` and
`Content-Disposition` headers that keep a stored file from executing on this
origin, and the fact that `/public/icons` is in the authentication bypass list
(`app/core/security.py`) while `/icons/upload` is not.

That is the security control `security_appsec-03` names, and none of it ran
under a request before this file.
"""

from __future__ import annotations

import struct
import zlib
from uuid import uuid4

import pytest
from fastapi import status

pytestmark = pytest.mark.e2e


def _png(width: int = 8, height: int = 8) -> bytes:
    """A real, decodable PNG, built rather than fixtured.

    `validate_raster_icon` parses the header and walks the chunks, so a stub of
    the right magic bytes is not enough -- it has to survive a CRC check.
    """

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (
            struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


async def test_an_uploaded_icon_is_served_back_inline_and_inert(
    authenticated_client, async_client
):
    upload = await authenticated_client.post(
        "/icons/upload", files={"file": ("icon.png", _png(), "image/png")}
    )
    assert upload.status_code == status.HTTP_201_CREATED, upload.text
    body = upload.json()
    assert body["content_type"] == "image/png"

    # Public: no session. `/public/icons` is in the auth bypass list, and an
    # icon that needs a cookie to render is not an icon.
    fetched = await async_client.get(f"/public/icons/{body['storage_path']}")

    assert fetched.status_code == status.HTTP_200_OK
    assert fetched.content == _png()
    assert fetched.headers["content-type"].startswith("image/png")
    # The header that stops a browser second-guessing the type we declared.
    assert fetched.headers["x-content-type-options"] == "nosniff"
    # A known-inert raster renders inline; only the unknown ones are forced to
    # download, which the next test covers.
    assert "content-disposition" not in fetched.headers


async def test_an_svg_is_refused_however_it_is_labelled(authenticated_client):
    # Three spellings of the same attempt. The sniff reads the bytes, so the
    # filename and the declared content type buy nothing -- which is the point:
    # SVG is XML, it can carry <script>, and it would execute on this origin.
    for filename, content_type in (
        ("icon.svg", "image/svg+xml"),
        ("icon.png", "image/png"),
        ("icon.png", "application/octet-stream"),
    ):
        response = await authenticated_client.post(
            "/icons/upload", files={"file": (filename, SVG, content_type)}
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, (
            f"{filename} as {content_type} was accepted"
        )
        assert "PNG, JPEG, GIF, WEBP, or BMP" in response.json()["message"]


async def test_an_empty_upload_is_refused(authenticated_client):
    response = await authenticated_client.post(
        "/icons/upload", files={"file": ("icon.png", b"", "image/png")}
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "empty" in response.json()["message"].lower()


async def test_a_png_header_over_a_malformed_body_is_refused(authenticated_client):
    # Past the sniff, into `validate_raster_icon`: the magic bytes are right and
    # everything after them is not. This is the path that parses attacker-
    # supplied bytes, so "rejected" rather than "raised" is the contract.
    malformed = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    response = await authenticated_client.post(
        "/icons/upload", files={"file": ("icon.png", malformed, "image/png")}
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


async def test_uploading_needs_a_session(async_client):
    response = await async_client.post(
        "/icons/upload", files={"file": ("icon.png", _png(), "image/png")}
    )
    # Serving is public; uploading is not.
    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }


async def test_a_path_that_climbs_out_is_refused(async_client):
    response = await async_client.get("/public/icons/../../etc/passwd")
    # 400 from the service's own path check, or 404 from the router refusing to
    # match -- either is a refusal; what must not happen is a file coming back.
    assert response.status_code in {
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_404_NOT_FOUND,
    }
    assert b"root:" not in response.content


async def test_a_name_that_is_not_a_storage_path_is_a_400(async_client):
    # `read_icon` matches the path against a shape -- `<user-uuid>/<asset-hex>.<ext>`
    # -- before it looks for a file. A name that is not even that shape is a bad
    # request, not a missing resource, and the two are different answers.
    response = await async_client.get("/public/icons/does-not-exist.png")
    assert response.status_code == status.HTTP_400_BAD_REQUEST


async def test_a_well_formed_but_absent_icon_is_a_404(async_client):
    # `icons/<user-uuid>/<32 hex>.<ext>` -- the shape
    # `_STORAGE_PATH_PATTERN` accepts, so this gets past the shape check and
    # reaches the lookup that finds nothing.
    absent = f"icons/{uuid4()}/{uuid4().hex}.png"
    response = await async_client.get(f"/public/icons/{absent}")
    assert response.status_code == status.HTTP_404_NOT_FOUND
