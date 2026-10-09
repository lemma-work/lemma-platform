"""A published app's links unfurl with a cover, over its real routes."""

from __future__ import annotations

import io
from urllib.parse import urlparse
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi import status
from PIL import Image

from app.core import app_install
from app.core.config import settings
from app.modules.apps.services.app_cover import COVER_HEIGHT, COVER_WIDTH

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _app_hosts(monkeypatch):
    monkeypatch.setattr(settings, "app_base_domain", "apps.test")


def host_of(slug: str) -> str:
    return f"{slug}.{settings.app_base_domain}"


def png(width: int, height: int) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), (90, 63, 212)).save(output, format="PNG")
    return output.getvalue()


def dist_archive(extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "index.html",
            "<!doctype html><html><head></head><body>hello</body></html>",
        )
        for path, content in (extra or {}).items():
            archive.writestr(path, content)
    return buffer.getvalue()


async def publish_app(authenticated_client, pod_id: str, archive: bytes) -> str:
    app_name = f"app_cover_{uuid4().hex[:8]}"
    public_slug = f"cover-app-{uuid4().hex[:8]}"
    created = await authenticated_client.post(
        f"/pods/{pod_id}/apps",
        json={
            "name": app_name,
            "public_slug": public_slug,
            "description": "Every open deal and who owns it",
        },
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    uploaded = await authenticated_client.post(
        f"/pods/{pod_id}/apps/{app_name}/bundle",
        files={"dist_archive": ("dist.zip", archive, "application/zip")},
    )
    assert uploaded.status_code == status.HTTP_200_OK, uploaded.text
    return public_slug


@pytest.mark.asyncio
async def test_an_app_with_no_cover_unfurls_with_one_the_host_draws(
    async_client,
    authenticated_client,
    test_pod,
):
    slug = await publish_app(authenticated_client, test_pod["id"], dist_archive())
    headers = {"host": host_of(slug)}

    page = await async_client.get("/", headers=headers)
    assert page.status_code == status.HTTP_200_OK, page.text
    scheme = urlparse(settings.api_url).scheme or "https"
    cover_url = f"{scheme}://{host_of(slug)}{app_install.COVER_PATH}"
    assert f'property="og:image" content="{cover_url}"' in page.text

    cover = await async_client.get(app_install.COVER_PATH, headers=headers)
    assert cover.status_code == status.HTTP_200_OK, cover.text
    assert cover.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(cover.content)).size == (COVER_WIDTH, COVER_HEIGHT)
    # One address, a picture that changes with every release and rename: it
    # must revalidate, never sit in a cache as an immutable asset.
    assert "no-cache" in cover.headers["cache-control"]
    assert "immutable" not in cover.headers["cache-control"]

    unchanged = await async_client.get(
        app_install.COVER_PATH,
        headers={**headers, "If-None-Match": cover.headers["etag"]},
    )
    assert unchanged.status_code == status.HTTP_304_NOT_MODIFIED


@pytest.mark.asyncio
async def test_a_build_that_ships_a_cover_is_served_its_own(
    async_client,
    authenticated_client,
    test_pod,
):
    own = png(COVER_WIDTH, COVER_HEIGHT)
    slug = await publish_app(
        authenticated_client,
        test_pod["id"],
        dist_archive({app_install.COVER_PATH.lstrip("/"): own}),
    )

    cover = await async_client.get(
        app_install.COVER_PATH, headers={"host": host_of(slug)}
    )

    assert cover.status_code == status.HTTP_200_OK, cover.text
    assert cover.content == own
    assert "immutable" not in cover.headers["cache-control"]


@pytest.mark.asyncio
async def test_an_unpublished_app_shows_no_cover(
    async_client,
    authenticated_client,
    test_pod,
):
    # The drawn cover carries the app's name and description, and the build's
    # own carries its first screen, so both stay behind the page's PUBLIC gate.
    pod_id = test_pod["id"]
    slug = await publish_app(authenticated_client, pod_id, dist_archive())
    listed = (await authenticated_client.get(f"/pods/{pod_id}/apps")).json()
    app_name = next(
        entry["name"] for entry in listed["items"] if entry["public_slug"] == slug
    )
    unpublished = await authenticated_client.patch(
        f"/pods/{pod_id}/apps/{app_name}", json={"visibility": "POD"}
    )
    assert unpublished.status_code == status.HTTP_200_OK, unpublished.text

    cover = await async_client.get(
        app_install.COVER_PATH, headers={"host": host_of(slug)}
    )

    assert cover.status_code == status.HTTP_404_NOT_FOUND
