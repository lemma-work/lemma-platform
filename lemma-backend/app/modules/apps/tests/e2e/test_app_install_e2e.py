"""A published app is installable to a home screen, over its real routes."""

from __future__ import annotations

import io
import json
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi import status

from app.core import app_install

pytestmark = pytest.mark.e2e

_SLUG_HEADER = "X-App-Public-Slug"


def build_dist_archive() -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "index.html",
            "<!doctype html><html><head></head><body>hello</body></html>",
        )
    return buffer.getvalue()


async def publish_app(authenticated_client, pod_id: str) -> str:
    app_name = f"app_install_{uuid4().hex[:8]}"
    public_slug = f"install-app-{uuid4().hex[:8]}"

    created = await authenticated_client.post(
        f"/pods/{pod_id}/apps",
        json={
            "name": app_name,
            "public_slug": public_slug,
            "description": "installable check",
        },
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text

    uploaded = await authenticated_client.post(
        f"/pods/{pod_id}/apps/{app_name}/bundle",
        files={"dist_archive": ("dist.zip", build_dist_archive(), "application/zip")},
    )
    assert uploaded.status_code == status.HTTP_200_OK, uploaded.text
    return public_slug


@pytest.mark.asyncio
async def test_published_app_serves_what_an_install_needs(
    async_client,
    authenticated_client,
    test_pod,
):
    slug = await publish_app(authenticated_client, test_pod["id"])
    headers = {_SLUG_HEADER: slug}

    entrypoint = await async_client.get("/public/apps", headers=headers)
    assert entrypoint.status_code == status.HTTP_200_OK, entrypoint.text
    assert f'href="{app_install.MANIFEST_PATH}"' in entrypoint.text
    assert app_install.APP_INSTALL_SENTINEL in entrypoint.text

    manifest = await async_client.get(
        f"/public/apps{app_install.MANIFEST_PATH}", headers=headers
    )
    assert manifest.status_code == status.HTTP_200_OK, manifest.text
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    body = json.loads(manifest.text)
    assert body["start_url"] == "/"
    assert body["display"] == "standalone"

    # Revalidation, so a rebuild does not re-download an unchanged manifest.
    unchanged = await async_client.get(
        f"/public/apps{app_install.MANIFEST_PATH}",
        headers={**headers, "If-None-Match": manifest.headers["etag"]},
    )
    assert unchanged.status_code == status.HTTP_304_NOT_MODIFIED

    worker = await async_client.get(
        f"/public/apps{app_install.SERVICE_WORKER_PATH}", headers=headers
    )
    assert worker.status_code == status.HTTP_200_OK, worker.text
    # Without this the browser refuses the "/" registration the script asks for,
    # and with no worker Chromium never offers the install at all.
    assert worker.headers["service-worker-allowed"] == "/"

    icon = await async_client.get(
        f"/public/apps{app_install.ICON_PATH_TEMPLATE.format(size=512)}",
        headers=headers,
    )
    assert icon.status_code == status.HTTP_200_OK
    assert icon.headers["content-type"] == "image/png"

    offline = await async_client.get(
        f"/public/apps{app_install.OFFLINE_PATH}", headers=headers
    )
    assert offline.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_an_unpublished_app_does_not_describe_itself_to_the_world(
    async_client,
    authenticated_client,
    test_pod,
):
    # The manifest carries the app's name and description and is served with no
    # session, so the reserved paths have to sit behind the same PUBLIC gate the
    # page does -- otherwise unpublishing would leave the name readable.
    pod_id = test_pod["id"]
    slug = await publish_app(authenticated_client, pod_id)
    app_name = (await authenticated_client.get(f"/pods/{pod_id}/apps")).json()
    app_name = next(
        entry["name"] for entry in app_name["items"] if entry["public_slug"] == slug
    )

    unpublished = await authenticated_client.patch(
        f"/pods/{pod_id}/apps/{app_name}",
        json={"visibility": "POD"},
    )
    assert unpublished.status_code == status.HTTP_200_OK, unpublished.text

    for path in (
        app_install.MANIFEST_PATH,
        app_install.SERVICE_WORKER_PATH,
        app_install.ICON_PATH_TEMPLATE.format(size=512),
    ):
        response = await async_client.get(
            f"/public/apps{path}", headers={_SLUG_HEADER: slug}
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND, path
