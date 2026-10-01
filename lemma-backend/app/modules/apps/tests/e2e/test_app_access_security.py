"""Private app hosts against real authorization, Redis and identity services."""

from __future__ import annotations

import io
import socket
import time
from dataclasses import dataclass, replace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import update
from supertokens_python.exceptions import SuperTokensError
from supertokens_python.recipe.session.asyncio import revoke_session
from supertokens_python.recipe.session.recipe import SessionRecipe

from app.core.auth_state_cache import invalidate_auth_state
from app.core.config import settings
from app.modules.apps.config import apps_settings
from app.modules.apps.services.app_access import (
    AppAccessClaims,
    AppAccessPurpose,
    mint_app_access_token,
    verify_app_access_token,
)
from app.modules.apps.tests.e2e.test_app_e2e import build_dist_archive
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.infrastructure.supertokens_auth.helpers import get_user_token
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)
from app.modules.test_support.e2e_authz import (
    auth_headers,
    create_role_visibility_context,
    signup_user,
)

pytestmark = pytest.mark.e2e
ACCESS_COOKIE = "__Host-lemmaAppAccess"
CONTENT = "PRIVATE_APP_CONTENT"


@dataclass(frozen=True)
class HostedApp:
    pod_id: str
    name: str
    origin: str


@pytest.fixture(autouse=True)
def check_every_request(monkeypatch):
    """Revocation tests need the live check; the cache test turns it back on."""
    monkeypatch.setattr(apps_settings, "app_access_cache_ttl_seconds", 0)


@pytest_asyncio.fixture
async def browser(test_app):
    async with AsyncClient(
        transport=ASGITransport(app=test_app, client=("192.0.2.1", 443)),
        base_url="https://api.example.test",
    ) as client:
        yield client


@pytest_asyncio.fixture
async def hosted_app(request, authenticated_client, test_pod, monkeypatch):
    monkeypatch.setattr(settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(settings, "app_base_domain", "apps.example.test")
    monkeypatch.setattr(settings, "frontend_url", "https://workspace.example.test")
    monkeypatch.setattr(settings, "app_api_via_app_origin", False)
    name = f"private-{uuid4().hex[:8]}"
    pod_id = test_pod["id"]
    created = await authenticated_client.post(
        f"/pods/{pod_id}/apps",
        json={
            "name": name,
            "public_slug": name,
            "visibility": getattr(request, "param", "POD"),
        },
    )
    assert created.status_code == 201, created.text
    await _upload(authenticated_client, pod_id, name, build_dist_archive(CONTENT))
    return HostedApp(pod_id, name, f"https://{name}.apps.example.test")


async def _upload(client: AsyncClient, pod_id: str, name: str, archive: bytes) -> None:
    uploaded = await client.post(
        f"/pods/{pod_id}/apps/{name}/bundle",
        files={"dist_archive": ("dist.zip", archive, "application/zip")},
    )
    assert uploaded.status_code == 200, uploaded.text


async def ticket(
    client: AsyncClient, origin: str | None, headers: dict[str, str] | None = None
) -> Response:
    sent = dict(headers or {})
    if origin is not None:
        sent["Origin"] = origin
    return await client.post("/apps/access/tickets", headers=sent)


async def redeem(
    browser: AsyncClient, host: str, value: str, *, origin: str | None = None
) -> Response:
    return await browser.post(
        host + "/_lemma/app-access/redeem",
        headers={"Origin": origin if origin is not None else host},
        json={"ticket": value},
    )


async def establish(
    browser: AsyncClient,
    client: AsyncClient,
    origin: str,
    headers: dict[str, str] | None = None,
) -> AppAccessClaims:
    issued = await ticket(client, origin, headers)
    assert issued.status_code == 200, issued.text
    assert issued.headers["cache-control"] == "private, no-store"
    assert issued.json()["expires_in_seconds"] == 60
    redeemed = await redeem(browser, origin, issued.json()["ticket"])
    assert redeemed.status_code == 200, redeemed.text
    cookie = redeemed.headers["set-cookie"]
    assert cookie.startswith(ACCESS_COOKIE + "=")
    assert "Domain=" not in cookie
    assert all(
        flag in cookie for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/")
    )
    claims = verify_app_access_token(
        redeemed.cookies[ACCESS_COOKIE],
        purpose=AppAccessPurpose.COOKIE,
        origin=origin,
    )
    assert claims is not None
    return claims


def assert_gate(response: Response) -> None:
    assert response.status_code == 401, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert CONTENT not in response.text


@pytest.mark.parametrize("hosted_app", ["POD", "PERSONAL", "RESTRICTED"], indirect=True)
async def test_visibility_requires_the_real_app_permission(
    browser, authenticated_client, hosted_app
):
    app = hosted_app
    page = await browser.get(
        app.origin + "/deep/path?mode=study", headers={"Accept": "text/html"}
    )
    assert_gate(page)
    assert "Open this app" in page.text and app.name not in page.text
    for path in ["/assets/app.js", "/styles.css"]:
        denied = await browser.get(app.origin + path, headers={"Accept": "text/html"})
        assert_gate(denied)
        assert "<!doctype" not in denied.text.lower()

    outsider = await signup_user(browser, "private-outsider")
    refused = await ticket(browser, app.origin, auth_headers(outsider))
    assert refused.status_code == 404 and app.name not in refused.text

    await establish(browser, authenticated_client, app.origin)
    for path in ["/", "/deep/path?mode=study", "/assets/app.js"]:
        opened = await browser.get(app.origin + path)
        assert opened.status_code == 200, opened.text
        assert opened.headers["cache-control"] == "private, no-cache"
        assert opened.headers["x-robots-tag"] == "noindex"
        revalidated = await browser.get(
            app.origin + path, headers={"If-None-Match": opened.headers["etag"]}
        )
        assert revalidated.status_code == 304
        assert revalidated.headers["cache-control"] == "private, no-cache"

    api_asset = await authenticated_client.get(
        f"/pods/{app.pod_id}/apps/{app.name}/assets"
    )
    assert api_asset.status_code == 200
    assert api_asset.headers["cache-control"] == "private, no-cache"
    assert "etag" in api_asset.headers

    # The app's cookie opens its files, and nothing on the API.
    assert (await browser.get("https://api.example.test/users/me")).status_code == 401


async def test_a_conditional_read_without_the_cookie_is_refused(browser, hosted_app):
    refused = await browser.get(
        hosted_app.origin + "/assets/app.js", headers={"If-None-Match": "*"}
    )
    assert_gate(refused)


async def test_missing_app_is_indistinguishable_from_a_private_one(
    browser, hosted_app, authenticated_client
):
    missing = "https://missing.apps.example.test"
    known = await browser.get(hosted_app.origin + "/", headers={"Accept": "text/html"})
    absent = await browser.get(missing + "/", headers={"Accept": "text/html"})
    assert known.status_code == absent.status_code == 401
    assert known.content == absent.content

    outsider = await signup_user(browser, "private-guesser")
    forbidden = await ticket(browser, hosted_app.origin, auth_headers(outsider))
    nonexistent = await ticket(authenticated_client, missing)
    assert forbidden.status_code == nonexistent.status_code == 404
    assert forbidden.json() == nonexistent.json()


@pytest.mark.parametrize("asset_path", ["reports.html", "nested/index.html"])
async def test_private_html_navigation_can_sign_in_at_its_original_path(
    browser, hosted_app, authenticated_client, asset_path
):
    archive = io.BytesIO(build_dist_archive(CONTENT))
    with ZipFile(archive, "a") as bundle:
        bundle.writestr(asset_path, "<html><body>PRIVATE_REPORT</body></html>")
    await _upload(
        authenticated_client, hosted_app.pod_id, hosted_app.name, archive.getvalue()
    )
    url = hosted_app.origin + "/" + asset_path + "?period=current"
    gate = await browser.get(url, headers={"Accept": "text/html"})
    assert_gate(gate)
    assert "Open this app" in gate.text and "PRIVATE_REPORT" not in gate.text
    script = await browser.get(
        url, headers={"Accept": "text/html", "Sec-Fetch-Dest": "script"}
    )
    assert script.status_code == 401
    assert "text/html" not in script.headers["content-type"]

    await establish(browser, authenticated_client, hosted_app.origin)
    opened = await browser.get(url, headers={"Accept": "text/html"})
    assert opened.status_code == 200 and "PRIVATE_REPORT" in opened.text


async def test_private_missing_document_navigation_offers_workspace_recovery(
    browser, hosted_app, authenticated_client
):
    await establish(browser, authenticated_client, hosted_app.origin)
    url = hosted_app.origin + "/library/report.pdf"
    navigation = await browser.get(url, headers={"Accept": "text/html"})
    assert navigation.status_code == 404
    assert "text/html" in navigation.headers["content-type"], navigation.text
    assert "Open it in your workspace" in navigation.text
    assert "library/report.pdf" in navigation.text
    assert "no-store" in navigation.headers["cache-control"]
    asset = await browser.get(url, headers={"Accept": "application/pdf"})
    assert asset.status_code == 404
    assert "application/json" in asset.headers["content-type"]


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "http://{name}.apps.example.test",
        "https://workspace.example.test",
        "https://api.example.test",
        "https://{name}.apps.example.test/",
        "https://{name}.apps.example.test:8443",
        "https://x.{name}.apps.example.test",
        "https://apps.example.test",
    ],
)
async def test_a_ticket_names_the_app_by_its_exact_origin(
    authenticated_client, hosted_app, origin
):
    sent = origin.format(name=hosted_app.name) if origin is not None else None
    refused = await ticket(authenticated_client, sent)
    assert refused.status_code == 404, refused.text
    assert refused.headers["cache-control"] == "private, no-store"
    assert "ticket" not in refused.json()


async def test_a_ticket_needs_the_persons_own_session(
    browser, test_app, authenticated_client, hosted_app
):
    unauthenticated = await ticket(browser, hosted_app.origin)
    assert unauthenticated.status_code == 401

    me = await authenticated_client.get("/users/me")
    user_id = UUID(me.json()["id"])
    token = await get_user_token(
        user_id,
        delegation_claims=build_delegation_claims(
            workload_type="agent",
            workload_id=uuid4(),
            pod_id=UUID(hosted_app.pod_id),
            session_id=uuid4().hex,
            invoked_by_user_id=user_id,
            workload_name="assistant",
        ),
    )
    delegated = await ticket(
        browser, hosted_app.origin, {"Authorization": f"Bearer {token}"}
    )
    assert delegated.status_code in {403, 404}, delegated.text
    assert "ticket" not in delegated.text


@pytest.mark.parametrize("attack", ["tampered", "other-origin", "header", "cookie"])
async def test_redemption_takes_only_a_ticket_for_this_origin(
    browser, hosted_app, authenticated_client, attack
):
    issued = await ticket(authenticated_client, hosted_app.origin)
    value = issued.json()["ticket"]
    host, origin = hosted_app.origin, hosted_app.origin
    if attack == "tampered":
        value = value[:-2] + ("AA" if not value.endswith("AA") else "BB")
    elif attack == "other-origin":
        host = origin = "https://other.apps.example.test"
    elif attack == "header":
        origin = "https://workspace.example.test"
    else:
        claims = verify_app_access_token(
            value, purpose=AppAccessPurpose.TICKET, origin=hosted_app.origin
        )
        assert claims is not None
        value = mint_app_access_token(AppAccessPurpose.COOKIE, claims)
    refused = await redeem(browser, host, value, origin=origin)
    assert refused.status_code == 401, refused.text
    assert ACCESS_COOKIE not in refused.headers.get("set-cookie", "")


async def test_expired_ticket_and_ticket_as_cookie_are_refused(
    browser, hosted_app, authenticated_client
):
    claims = await establish(browser, authenticated_client, hosted_app.origin)
    expired = mint_app_access_token(
        AppAccessPurpose.TICKET, replace(claims, expires_at=int(time.time()) - 1)
    )
    assert (await redeem(browser, hosted_app.origin, expired)).status_code == 401

    as_cookie = mint_app_access_token(AppAccessPurpose.TICKET, claims)
    browser.cookies.clear()
    refused = await browser.get(
        hosted_app.origin + "/assets/app.js",
        headers={"Cookie": f"{ACCESS_COOKIE}={as_cookie}"},
    )
    assert_gate(refused)


async def test_a_cookie_opens_only_the_app_and_host_it_was_issued_for(
    browser, hosted_app, authenticated_client, test_pod
):
    claims = await establish(browser, authenticated_client, hosted_app.origin)
    other = f"other-{uuid4().hex[:8]}"
    created = await authenticated_client.post(
        f"/pods/{test_pod['id']}/apps",
        json={"name": other, "public_slug": other, "visibility": "POD"},
    )
    assert created.status_code == 201, created.text
    await _upload(authenticated_client, test_pod["id"], other, build_dist_archive("B"))

    # The same cookie value, presented to the other app's host and to a preview.
    value = mint_app_access_token(AppAccessPurpose.COOKIE, claims)
    for host in (
        f"https://{other}.apps.example.test",
        f"https://{hosted_app.name}--r1.apps.example.test",
    ):
        refused = await browser.get(
            host + "/assets/app.js", headers={"Cookie": f"{ACCESS_COOKIE}={value}"}
        )
        assert_gate(refused)

    # Re-signed for the other host, it still names the first app.
    moved = replace(claims, origin=f"https://{other}.apps.example.test")
    refused = await browser.get(
        moved.origin + "/assets/app.js",
        headers={
            "Cookie": f"{ACCESS_COOKIE}="
            + mint_app_access_token(AppAccessPurpose.COOKIE, moved)
        },
    )
    assert_gate(refused)


async def test_sign_out_and_account_disable_end_access(
    browser, hosted_app, authenticated_client
):
    from app.core.infrastructure.db.session import async_session_maker

    claims = await establish(browser, authenticated_client, hosted_app.origin)

    async def set_active(active: bool) -> None:
        async with async_session_maker() as session:
            await session.execute(
                update(User).where(User.id == claims.user_id).values(is_active=active)
            )
            await session.commit()
        await invalidate_auth_state(claims.user_id)

    await set_active(False)
    try:
        assert_gate(await browser.get(hosted_app.origin + "/assets/app.js"))
    finally:
        await set_active(True)
    assert (await browser.get(hosted_app.origin + "/")).status_code == 200

    await revoke_session(claims.session_handle)
    assert_gate(await browser.get(hosted_app.origin + "/assets/app.js"))


async def test_public_to_private_stops_anonymous_and_conditional_reads(
    browser, hosted_app, authenticated_client
):
    path = f"/pods/{hosted_app.pod_id}/apps/{hosted_app.name}"
    public = await authenticated_client.patch(path, json={"visibility": "PUBLIC"})
    assert public.status_code == 200, public.text
    opened = await browser.get(hosted_app.origin + "/assets/app.js")
    assert opened.status_code == 200
    assert opened.headers["cache-control"] == "public, max-age=31536000, immutable"
    etag = opened.headers["etag"]
    conditional = {"If-None-Match": etag}
    assert (
        await browser.get(hosted_app.origin + "/assets/app.js", headers=conditional)
    ).status_code == 304

    private = await authenticated_client.patch(path, json={"visibility": "POD"})
    assert private.status_code == 200
    assert_gate(
        await browser.get(hosted_app.origin + "/assets/app.js", headers=conditional)
    )
    await establish(browser, authenticated_client, hosted_app.origin)
    reopened = await browser.get(
        hosted_app.origin + "/assets/app.js", headers=conditional
    )
    assert reopened.status_code == 304
    assert reopened.headers["cache-control"] == "private, no-cache"


async def test_a_public_app_ignores_a_stale_cookie(
    browser, hosted_app, authenticated_client, monkeypatch
):
    await establish(browser, authenticated_client, hosted_app.origin)
    path = f"/pods/{hosted_app.pod_id}/apps/{hosted_app.name}"
    assert (
        await authenticated_client.patch(path, json={"visibility": "PUBLIC"})
    ).status_code == 200
    implementation = SessionRecipe.get_instance().recipe_implementation
    monkeypatch.setattr(
        implementation,
        "get_session_information",
        AsyncMock(side_effect=SuperTokensError("down")),
    )
    opened = await browser.get(hosted_app.origin + "/assets/app.js")
    assert opened.status_code == 200
    assert opened.headers["cache-control"].startswith("public")


async def test_restricted_grant_revocation_and_preview_update_permission(
    browser, authenticated_client, async_client, fixed_test_org, monkeypatch
):
    monkeypatch.setattr(settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(settings, "app_base_domain", "apps.example.test")
    ctx = await create_role_visibility_context(
        authenticated_client,
        async_client,
        fixed_test_org,
        pod_name_prefix="host-access",
        custom_role="APP_READERS",
    )
    name = f"restricted-{uuid4().hex[:8]}"
    created = await authenticated_client.post(
        f"/pods/{ctx['pod_id']}/apps",
        json={"name": name, "public_slug": name, "visibility": "RESTRICTED"},
    )
    assert created.status_code == 201, created.text
    await _upload(
        authenticated_client, ctx["pod_id"], name, build_dist_archive(CONTENT)
    )
    grants_path = f"/pods/{ctx['pod_id']}/roles/{ctx['custom_role']}/permissions"
    granted = await authenticated_client.put(
        grants_path,
        json={
            "grants": [
                {
                    "resource_type": "app",
                    "resource_name": name,
                    "permission_ids": ["app.read"],
                }
            ]
        },
    )
    assert granted.status_code == 200, granted.text
    origin = f"https://{name}.apps.example.test"
    await establish(browser, async_client, origin, ctx["custom_headers"])
    assert (await browser.get(origin + "/")).status_code == 200

    preview = f"https://{name}--r1.apps.example.test"
    denied = await ticket(async_client, preview, ctx["custom_headers"])
    assert denied.status_code == 404, denied.text
    await establish(browser, authenticated_client, preview)
    assert (await browser.get(preview + "/")).status_code == 200

    revoked = await authenticated_client.put(grants_path, json={"grants": []})
    assert revoked.status_code == 200, revoked.text
    assert_gate(await browser.get(origin + "/assets/app.js"))


async def test_a_recent_check_is_reused_and_kept_per_session_and_app(
    browser, hosted_app, authenticated_client, monkeypatch
):
    monkeypatch.setattr(apps_settings, "app_access_cache_ttl_seconds", 60)
    claims = await establish(browser, authenticated_client, hosted_app.origin)
    assert (await browser.get(hosted_app.origin + "/")).status_code == 200

    implementation = SessionRecipe.get_instance().recipe_implementation
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(implementation, "get_session_information", lookup)
    # Within the window the earlier answer stands, without asking again.
    assert (await browser.get(hosted_app.origin + "/assets/app.js")).status_code == 200
    lookup.assert_not_called()

    # Another session for the same person and app is not covered by it.
    other = replace(claims, session_handle="another-session")
    refused = await browser.get(
        hosted_app.origin + "/assets/app.js",
        headers={
            "Cookie": f"{ACCESS_COOKIE}="
            + mint_app_access_token(AppAccessPurpose.COOKIE, other)
        },
    )
    assert_gate(refused)
    lookup.assert_awaited_once()


async def test_redis_outage_falls_back_to_the_live_check(
    browser, hosted_app, authenticated_client, monkeypatch
):
    await establish(browser, authenticated_client, hosted_app.origin)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    # A cache built now, at a TTL no earlier test used, talks to nothing.
    monkeypatch.setattr(settings, "redis_url", f"redis://127.0.0.1:{port}/0")
    monkeypatch.setattr(apps_settings, "app_access_cache_ttl_seconds", 59)
    opened = await browser.get(hosted_app.origin + "/assets/app.js")
    assert opened.status_code == 200, opened.text


async def test_identity_outage_cannot_serve_private_content(
    browser, hosted_app, authenticated_client, monkeypatch
):
    await establish(browser, authenticated_client, hosted_app.origin)
    implementation = SessionRecipe.get_instance().recipe_implementation
    monkeypatch.setattr(
        implementation,
        "get_session_information",
        AsyncMock(side_effect=SuperTokensError("provider secret")),
    )
    failed = await browser.get(hosted_app.origin + "/assets/app.js")
    assert failed.status_code == 503
    assert failed.headers["cache-control"] == "private, no-store"
    assert "provider secret" not in failed.text and CONTENT not in failed.text
    # Unknown is not revoked: the cookie survives for the next try.
    assert ACCESS_COOKIE not in failed.headers.get("set-cookie", "")


@pytest.mark.parametrize("parent_state", ["expired", "wrong-user", "missing"])
async def test_the_session_behind_the_cookie_is_rechecked(
    browser, hosted_app, authenticated_client, monkeypatch, parent_state
):
    claims = await establish(browser, authenticated_client, hosted_app.origin)
    implementation = SessionRecipe.get_instance().recipe_implementation
    information = await implementation.get_session_information(
        claims.session_handle, {}
    )
    assert information is not None
    if parent_state == "expired":
        information.expiry = int(time.time() * 1000) - 1
    elif parent_state == "wrong-user":
        information.user_id = str(uuid4())
    else:
        information = None
    monkeypatch.setattr(
        implementation, "get_session_information", AsyncMock(return_value=information)
    )
    assert_gate(await browser.get(hosted_app.origin + "/assets/app.js"))


async def test_access_paths_do_not_expose_the_general_api(browser, hosted_app):
    # The redeem door is only on app hosts.
    on_api = await browser.post(
        "https://api.example.test/public/apps/_lemma/app-access/redeem",
        headers={"Origin": hosted_app.origin, "X-App-Public-Slug": hosted_app.name},
        json={"ticket": "x"},
    )
    assert on_api.status_code == 401
    assert (
        await browser.post(hosted_app.origin + "/_lemma/users/me")
    ).status_code != 200
