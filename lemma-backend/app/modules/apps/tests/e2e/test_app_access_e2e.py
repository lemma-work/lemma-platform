from __future__ import annotations

import base64
import hashlib
from uuid import uuid4

import pytest

from app.core.config import settings
from app.modules.apps.tests.e2e.test_app_e2e import build_dist_archive

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_private_app_host_can_establish_access(
    async_client, authenticated_client, test_pod, monkeypatch
):
    monkeypatch.setattr(settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(settings, "app_base_domain", "apps.example.test")
    slug = f"private-{uuid4().hex[:8]}"
    marker = "PRIVATE_APP_CONTENT"
    created = await authenticated_client.post(
        f"/pods/{test_pod['id']}/apps",
        json={"name": slug, "public_slug": slug, "visibility": "POD"},
    )
    assert created.status_code == 201, created.text
    uploaded = await authenticated_client.post(
        f"/pods/{test_pod['id']}/apps/{slug}/bundle",
        files={
            "dist_archive": ("dist.zip", build_dist_archive(marker), "application/zip")
        },
    )
    assert uploaded.status_code == 200, uploaded.text
    origin = f"https://{slug}.apps.example.test"
    verifier = "a" * 64
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    started = await async_client.post(
        origin + "/_lemma/app-access/requests",
        headers={"Origin": origin},
        json={"challenge": challenge},
    )
    assert started.status_code == 200, started.text
    authorized = await authenticated_client.post(
        f"/apps/access/requests/{started.json()['request_id']}/authorize",
        headers={"Origin": origin},
        json={"app_origin": origin},
    )
    assert authorized.status_code == 200, authorized.text
    redeemed = await async_client.post(
        origin + "/_lemma/app-access/redeem",
        headers={"Origin": origin},
        json={
            "request_id": started.json()["request_id"],
            "code": authorized.json()["code"],
            "verifier": verifier,
        },
    )
    assert redeemed.status_code == 200, redeemed.text
    opened = await async_client.get(origin + "/")
    assert opened.status_code == 200, opened.text
    assert marker in opened.text
    assert opened.headers["cache-control"] == "private, no-store"
