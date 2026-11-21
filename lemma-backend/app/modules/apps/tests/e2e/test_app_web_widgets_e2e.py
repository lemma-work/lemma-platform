"""A hosted app is told its pod's web widgets, over its real routes.

A Public app embeds its pod's chat and forms through a web widget, and had no
way to learn the widget's key: builders put it in every link as ``?k=``. The
key is publishable, so the host puts it in ``window.__LEMMA_CONFIG__`` -- but
only a widget that is switched on and allows the app's own origin, since any
other would refuse the page anyway.
"""

from __future__ import annotations

import io
import json
import re
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi import status

from app.core.config import settings
from app.core.public_web import public_web_settings

pytestmark = pytest.mark.e2e

API = "https://api.widgets-e2e.test"


@pytest.fixture(autouse=True)
def _hosted_apps_with_public_web(monkeypatch):
    monkeypatch.setattr(settings, "app_base_domain", "apps.test")
    # An https API makes the app's origin https too: a widget names only
    # https:// origins outside the developer's own machine.
    monkeypatch.setattr(settings, "api_url", "https://api.widgets-e2e.test")
    monkeypatch.setattr(public_web_settings, "public_web_enabled", True)


def _dist() -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "index.html",
            "<!doctype html><html><head></head><body>book a call</body></html>",
        )
    return buffer.getvalue()


async def _publish(client, pod_id: str) -> str:
    name = f"booking_{uuid4().hex[:8]}"
    slug = f"booking-{uuid4().hex[:8]}"
    created = await client.post(
        f"/pods/{pod_id}/apps",
        json={"name": name, "public_slug": slug, "visibility": "PUBLIC"},
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    uploaded = await client.post(
        f"/pods/{pod_id}/apps/{name}/bundle",
        files={"dist_archive": ("dist.zip", _dist(), "application/zip")},
    )
    assert uploaded.status_code == status.HTTP_200_OK, uploaded.text
    return slug


async def _widget(client, pod_id: str, name: str, **fields) -> dict:
    created = await client.post(
        f"/pods/{pod_id}/web-widgets",
        json={"name": name, "answer": "anyone", **fields},
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    return created.json()


def _config(page: str) -> dict:
    found = re.search(r"window\.__LEMMA_CONFIG__=(\{.*?\});</script>", page)
    assert found, "no runtime config was injected"
    return json.loads(found.group(1))


async def test_a_public_app_is_told_the_widgets_that_allow_its_page(
    async_client, authenticated_client, test_pod
):
    pod_id = test_pod["id"]
    slug = await _publish(authenticated_client, pod_id)
    host = {"host": f"{slug}.{settings.app_base_domain}"}

    alone = await async_client.get("/", headers=host)
    assert alone.status_code == status.HTTP_200_OK, alone.text
    assert "webWidgets" not in _config(alone.text)

    own = await _widget(
        authenticated_client,
        pod_id,
        "Booking",
        allowed_origins=[f"https://{slug}.{settings.app_base_domain}"],
    )
    anywhere = await _widget(authenticated_client, pod_id, "Anywhere")
    await _widget(
        authenticated_client,
        pod_id,
        "Shop",
        allowed_origins=["https://shop.example"],
    )
    await _widget(authenticated_client, pod_id, "Quiet", answer="off")

    page = await async_client.get(
        "/", headers={**host, "If-None-Match": alone.headers["etag"]}
    )
    # A widget made since the page was cached reaches it: not a 304.
    assert page.status_code == status.HTTP_200_OK, page.text
    assert _config(page.text)["webWidgets"] == [
        {
            "name": "Booking",
            "publicKey": own["public_key"],
            "scriptUrl": f"{API}/public/web/widget.js",
        },
        {
            "name": "Anywhere",
            "publicKey": anywhere["public_key"],
            "scriptUrl": f"{API}/public/web/widget.js",
        },
    ]

    switched_off = await authenticated_client.patch(
        f"/pods/{pod_id}/web-widgets/{anywhere['id']}", json={"answer": "off"}
    )
    assert switched_off.status_code == status.HTTP_200_OK, switched_off.text
    after = await async_client.get(
        "/", headers={**host, "If-None-Match": page.headers["etag"]}
    )
    assert after.status_code == status.HTTP_200_OK, after.text
    assert [w["name"] for w in _config(after.text)["webWidgets"]] == ["Booking"]


async def test_no_widget_is_offered_while_public_web_is_off(
    async_client, authenticated_client, test_pod, monkeypatch
):
    pod_id = test_pod["id"]
    slug = await _publish(authenticated_client, pod_id)
    await _widget(authenticated_client, pod_id, "Booking")

    monkeypatch.setattr(public_web_settings, "public_web_enabled", False)
    page = await async_client.get(
        "/", headers={"host": f"{slug}.{settings.app_base_domain}"}
    )
    assert page.status_code == status.HTTP_200_OK, page.text
    assert "webWidgets" not in _config(page.text)
