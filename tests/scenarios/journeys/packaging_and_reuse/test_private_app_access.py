"""Private builds reach their intended people through their existing address."""

from __future__ import annotations

import base64
import hashlib
import io
from urllib.parse import urlsplit
from zipfile import ZipFile

import httpx
import pytest

from harness import capability, covers, journey, proves, scenario
from journeys.packaging_and_reuse.test_app_publish import _dist_bytes

pytestmark = [journey("Packaging and reuse"), capability("Build an app")]


@scenario("A private app opens at its address only for a person with access")
@proves("PS-PACK-031")
@covers(
    "app.access.request.create",
    "app.access.request.authorize",
    "app.access.redeem",
    "app.asset.root.get",
)
@pytest.mark.parametrize("visibility", ["POD", "PERSONAL", "RESTRICTED"])
@pytest.mark.parametrize(
    "asset_path",
    [
        "/deep/path?mode=study",
        "/reports.html?period=current",
        "/nested/index.html?period=current",
    ],
)
async def test_private_app_address_requires_app_access(
    world, run, visibility, asset_path
):
    alice = await world.person("daniel")
    outsider = await world.person("hannah")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    app = await alice.api.post(
        f"/pods/{pod['id']}/apps",
        json={"name": run.name("app"), "visibility": visibility},
    )
    archive = io.BytesIO(_dist_bytes())
    with ZipFile(archive, "a") as bundle:
        for path in ["reports.html", "nested/index.html"]:
            bundle.writestr(path, "<html><body>scenario</body></html>")
    uploaded = await alice.api.call(
        "POST",
        f"/pods/{pod['id']}/apps/{app['name']}/bundle",
        files={"dist_archive": ("dist.zip", archive.getvalue(), "application/zip")},
    )
    assert uploaded.status_code == 200, uploaded.text[:300]
    url = uploaded.json()["app"]["url"]
    assert url and url.startswith("https://"), "This scenario needs hosted HTTPS app URLs"
    host = urlsplit(url).netloc
    origin = f"https://{host}"
    verifier = "v" * 64
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    headers = {"Host": host, "Origin": origin}
    async with httpx.AsyncClient(base_url=world.base_url, timeout=15) as browser:
        gate = await browser.get(asset_path, headers={**headers, "Accept": "text/html"})
        assert gate.status_code == 401 and "Open this app" in gate.text
        assert "<body>scenario</body>" not in gate.text
        started = await browser.post(
            "/_lemma/app-access/requests", headers=headers, json={"challenge": challenge}
        )
        assert started.status_code == 200, started.text[:300]
        pending = started.json()["request_id"]
        binding_name = f"__Host-lemmaAppAccessBinding-{pending}"
        binding = started.cookies[binding_name]
        denied = await outsider.api.call(
            "POST",
            f"/apps/access/requests/{pending}/authorize",
            headers={"Origin": origin},
            json={"app_origin": origin},
        )
        assert denied.status_code == 404 and app["name"] not in denied.text
        authorized = await alice.api.call(
            "POST",
            f"/apps/access/requests/{pending}/authorize",
            headers={"Origin": origin},
            json={"app_origin": origin},
        )
        assert authorized.status_code == 200, authorized.text[:300]
        redeemed = await browser.post(
            "/_lemma/app-access/redeem",
            headers={**headers, "Cookie": f"{binding_name}={binding}"},
            json={
                "request_id": pending,
                "code": authorized.json()["code"],
                "verifier": verifier,
            },
        )
        assert redeemed.status_code == 200, redeemed.text[:300]
        access = redeemed.cookies["__Host-lemmaAppAccess"]
        opened = await browser.get(
            asset_path,
            headers={
                **headers,
                "Cookie": f"__Host-lemmaAppAccess={access}",
                "If-None-Match": "*",
            },
        )
        assert opened.status_code == 200 and "<body>scenario</body>" in opened.text
        assert opened.headers["cache-control"] == "private, no-store"
        assert "etag" not in opened.headers
