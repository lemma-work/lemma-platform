"""Private apps on a local HTTP stack (`make dev`), against real authorization.

`make dev` serves the API and every app over plain HTTP. Before
``PRIVATE_APPS_OVER_HTTP`` only published apps opened there, so an app a plugin
installed -- private, like every imported app -- answered APP_NOT_FOUND inside
the web app. Opted in, the HTTPS flow runs unchanged on the ``.localhost`` app
host; only the cookie differs, because ``__Host-`` requires Secure.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.modules.apps.config import apps_settings
from app.modules.apps.tests.e2e.test_app_access_security import (
    CONTENT,
    _upload,
    assert_gate,
    redeem,
    ticket,
)
from app.modules.apps.tests.e2e.test_app_e2e import build_dist_archive
from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.test_support.e2e_authz import auth_headers, signup_user

pytestmark = pytest.mark.e2e

module_identity = e2e_fixtures.module_identity
fixed_test_user = e2e_fixtures.module_test_user_own_session
fixed_test_org = e2e_fixtures.module_test_org

PORT = 8710
API = f"http://api.lemma.localhost:{PORT}"
LOCAL_COOKIE = "lemmaAppAccess"


@dataclass(frozen=True)
class LocalApp:
    pod_id: str
    name: str
    origin: str


@pytest.fixture(autouse=True)
def check_every_request(monkeypatch):
    monkeypatch.setattr(apps_settings, "app_access_cache_ttl_seconds", 0)


@pytest_asyncio.fixture
async def browser(test_app):
    async with AsyncClient(
        transport=ASGITransport(app=test_app, client=("127.0.0.1", PORT)),
        base_url=API,
    ) as client:
        yield client


@pytest_asyncio.fixture
async def local_app(authenticated_client, test_pod, monkeypatch):
    monkeypatch.setattr(settings, "api_url", f"http://api.lemma.localhost:{PORT}")
    monkeypatch.setattr(settings, "app_base_domain", f"apps.lemma.localhost:{PORT}")
    monkeypatch.setattr(settings, "frontend_url", f"http://lemma.localhost:{PORT}")
    monkeypatch.setattr(settings, "app_api_via_app_origin", False)
    monkeypatch.setattr(apps_settings, "private_apps_over_http", True)
    name = f"local-{uuid4().hex[:8]}"
    created = await authenticated_client.post(
        f"/pods/{test_pod['id']}/apps",
        json={"name": name, "public_slug": name, "visibility": "POD"},
    )
    assert created.status_code == 201, created.text
    await _upload(
        authenticated_client, test_pod["id"], name, build_dist_archive(CONTENT)
    )
    return LocalApp(test_pod["id"], name, f"http://{name}.apps.lemma.localhost:{PORT}")


async def test_a_member_opens_a_private_app_over_local_http(
    browser, authenticated_client, local_app
):
    gate = await browser.get(
        local_app.origin + "/inbox", headers={"Accept": "text/html"}
    )
    assert_gate(gate)
    assert "Opening this app" in gate.text

    issued = await ticket(authenticated_client, local_app.origin)
    assert issued.status_code == 200, issued.text
    redeemed = await redeem(browser, local_app.origin, issued.json()["ticket"])
    assert redeemed.status_code == 200, redeemed.text

    cookie = redeemed.headers["set-cookie"]
    assert cookie.startswith(LOCAL_COOKIE + "=")
    assert all(flag in cookie for flag in ("HttpOnly", "SameSite=lax", "Path=/"))
    # Not Secure, or a browser drops it on http://; host-only, like the HTTPS one.
    assert (
        "Secure" not in cookie and "Domain=" not in cookie and "__Host-" not in cookie
    )

    for path in ["/", "/inbox", "/assets/app.js"]:
        opened = await browser.get(local_app.origin + path)
        assert opened.status_code == 200, opened.text
        assert opened.headers["cache-control"] == "private, no-cache"

    # The app's cookie opens its files, and nothing on the API.
    assert (await browser.get(API + "/users/me")).status_code == 401


async def test_a_non_member_still_cannot(browser, local_app):
    outsider = await signup_user(browser, "local-outsider")

    refused = await ticket(browser, local_app.origin, auth_headers(outsider))

    assert refused.status_code == 404 and local_app.name not in refused.text
    assert_gate(
        await browser.get(local_app.origin + "/", headers={"Accept": "text/html"})
    )


async def test_the_https_cookie_does_not_open_a_local_app(
    browser, authenticated_client, local_app
):
    """Each scheme reads its own name, so one never stands in for the other."""
    issued = await ticket(authenticated_client, local_app.origin)
    redeemed = await redeem(browser, local_app.origin, issued.json()["ticket"])
    value = redeemed.cookies[LOCAL_COOKIE]
    browser.cookies.clear()
    host = local_app.origin.removeprefix("http://").split(":")[0]
    browser.cookies.set("__Host-lemmaAppAccess", value, domain=host)

    assert_gate(
        await browser.get(local_app.origin + "/", headers={"Accept": "text/html"})
    )


async def test_without_the_setting_local_http_serves_published_apps_only(
    browser, authenticated_client, local_app, monkeypatch
):
    monkeypatch.setattr(apps_settings, "private_apps_over_http", False)

    page = await browser.get(local_app.origin + "/", headers={"Accept": "text/html"})
    assert page.status_code == 404
    assert page.json()["code"] == "APP_NOT_FOUND"
    assert (await ticket(authenticated_client, local_app.origin)).status_code == 404
