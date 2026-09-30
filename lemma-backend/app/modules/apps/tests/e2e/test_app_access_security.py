"""Host access against real authorization, Redis and identity services."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import hashlib
import io
import time
from uuid import uuid4
from zipfile import ZipFile

from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import update
from supertokens_python.recipe.session.asyncio import revoke_session

from app.core.config import settings
from app.core.infrastructure.redis.client import get_redis
from app.modules.apps.config import apps_settings
from app.modules.apps.domain.access import (
    AppAccessInvalidError,
    AppAccessRequest,
    AppAccessSession,
    AppAccessRateLimitedError,
)
from app.modules.apps.services.app_access_store import AppAccessStore, _key, token_hash
from app.modules.apps.tests.e2e.test_app_e2e import build_dist_archive
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.test_support.e2e_authz import (
    signup_user,
    auth_headers,
    create_role_visibility_context,
)

pytestmark = pytest.mark.e2e
VERIFIER = "v" * 64
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest())
    .decode()
    .rstrip("=")
)


@dataclass(frozen=True)
class HostedApp:
    pod_id: str
    name: str
    origin: str


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
    uploaded = await authenticated_client.post(
        f"/pods/{pod_id}/apps/{name}/bundle",
        files={
            "dist_archive": (
                "dist.zip",
                build_dist_archive("PRIVATE_APP_CONTENT"),
                "application/zip",
            )
        },
    )
    assert uploaded.status_code == 200, uploaded.text
    return HostedApp(pod_id, name, f"https://{name}.apps.example.test")


async def offer(browser: AsyncClient, origin: str) -> str:
    response = await browser.post(
        origin + "/_lemma/app-access/requests",
        headers={"Origin": origin},
        json={"challenge": CHALLENGE},
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["expires_in_seconds"] == 300
    return response.json()["request_id"]


async def approve(
    client: AsyncClient,
    request_id: str,
    origin: str,
    headers: dict[str, str] | None = None,
):
    return await client.post(
        f"/apps/access/requests/{request_id}/authorize",
        headers={"Origin": origin, **(headers or {})},
        json={"app_origin": origin},
    )


async def establish(
    browser: AsyncClient,
    client: AsyncClient,
    origin: str,
    headers: dict[str, str] | None = None,
) -> str:
    request_id = await offer(browser, origin)
    approved = await approve(client, request_id, origin, headers)
    assert approved.status_code == 200, approved.text
    redeemed = await browser.post(
        origin + "/_lemma/app-access/redeem",
        headers={"Origin": origin},
        json={
            "request_id": request_id,
            "code": approved.json()["code"],
            "verifier": VERIFIER,
        },
    )
    assert redeemed.status_code == 200, redeemed.text
    cookie = redeemed.headers["set-cookie"]
    assert (
        "Domain=" not in cookie
        and "HttpOnly" in cookie
        and "Secure" in cookie
        and "SameSite=lax" in cookie
        and "Path=/" in cookie
    )
    return redeemed.cookies["__Host-lemmaAppAccess"]


@pytest.mark.parametrize("hosted_app", ["POD", "PERSONAL", "RESTRICTED"], indirect=True)
async def test_visibility_requires_the_real_app_permission(
    browser, authenticated_client, hosted_app
):
    app = hosted_app
    bootstrap = await browser.get(
        app.origin + "/deep/path?mode=study", headers={"Accept": "text/html"}
    )
    assert bootstrap.status_code == 401 and "Open this app" in bootstrap.text
    assert (
        "PRIVATE_APP_CONTENT" not in bootstrap.text and app.name not in bootstrap.text
    )
    for path in ["/assets/app.js", "/styles.css"]:
        denied = await browser.get(app.origin + path, headers={"Accept": "text/html"})
        assert denied.status_code == 401 and "<!doctype" not in denied.text.lower()
        assert denied.headers["cache-control"] == "private, no-store"
    outsider = await signup_user(browser, "private-outsider")
    pending = await offer(browser, app.origin)
    denied = await approve(browser, pending, app.origin, auth_headers(outsider))
    assert denied.status_code == 404 and app.name not in denied.text
    await establish(browser, authenticated_client, app.origin)
    for path in ["/", "/deep/path?mode=study", "/assets/app.js"]:
        opened = await browser.get(app.origin + path, headers={"If-None-Match": "*"})
        assert opened.status_code == 200, opened.text
        assert opened.headers["cache-control"] == "private, no-store"
        assert "etag" not in opened.headers
    api_asset = await authenticated_client.get(
        f"/pods/{app.pod_id}/apps/{app.name}/assets", headers={"If-None-Match": "*"}
    )
    assert (
        api_asset.status_code == 200
        and api_asset.headers["cache-control"] == "private, no-store"
    )
    no_api_auth = await browser.get("https://api.example.test/users/me")
    assert no_api_auth.status_code == 401


async def test_missing_app_is_indistinguishable_before_authorization(
    browser, hosted_app, authenticated_client
):
    missing = "https://missing.apps.example.test"
    known = await browser.get(hosted_app.origin + "/", headers={"Accept": "text/html"})
    absent = await browser.get(missing + "/", headers={"Accept": "text/html"})
    assert known.status_code == absent.status_code == 401
    assert known.content == absent.content
    pending = await offer(browser, missing)
    refused = await approve(authenticated_client, pending, missing)
    assert refused.status_code == 404 and "missing" not in refused.text


@pytest.mark.parametrize("asset_path", ["reports.html", "nested/index.html"])
async def test_private_html_navigation_can_sign_in_at_its_original_path(
    browser, hosted_app, authenticated_client, asset_path
):
    archive = io.BytesIO(build_dist_archive("PRIVATE_APP_CONTENT"))
    with ZipFile(archive, "a") as bundle:
        bundle.writestr(asset_path, "<html><body>PRIVATE_REPORT</body></html>")
    uploaded = await authenticated_client.post(
        f"/pods/{hosted_app.pod_id}/apps/{hosted_app.name}/bundle",
        files={"dist_archive": ("dist.zip", archive.getvalue(), "application/zip")},
    )
    assert uploaded.status_code == 200, uploaded.text
    url = hosted_app.origin + "/" + asset_path + "?period=current"
    gate = await browser.get(url, headers={"Accept": "text/html"})
    assert gate.status_code == 401 and "Open this app" in gate.text, gate.text
    assert "PRIVATE_REPORT" not in gate.text
    script = await browser.get(
        url, headers={"Accept": "text/html", "Sec-Fetch-Dest": "script"}
    )
    assert (
        script.status_code == 401 and "text/html" not in script.headers["content-type"]
    )
    await establish(browser, authenticated_client, hosted_app.origin)
    opened = await browser.get(url, headers={"Accept": "text/html"})
    assert opened.status_code == 200 and "PRIVATE_REPORT" in opened.text
    assert opened.headers["cache-control"] == "private, no-store"


async def test_concurrent_first_handoffs_both_redeem_in_one_browser(
    browser, hosted_app, authenticated_client
):
    pending = await asyncio.gather(
        *(offer(browser, hosted_app.origin) for _ in range(2))
    )
    approved = await asyncio.gather(
        *(
            approve(authenticated_client, request_id, hosted_app.origin)
            for request_id in pending
        )
    )
    assert all(response.status_code == 200 for response in approved)
    redeemed = await asyncio.gather(
        *(
            browser.post(
                hosted_app.origin + "/_lemma/app-access/redeem",
                headers={"Origin": hosted_app.origin},
                json={
                    "request_id": request_id,
                    "code": response.json()["code"],
                    "verifier": VERIFIER,
                },
            )
            for request_id, response in zip(pending, approved, strict=True)
        )
    )
    assert [response.status_code for response in redeemed] == [200, 200]
    assert not any(
        cookie.name.startswith("__Host-lemmaAppAccessBinding-")
        for cookie in browser.cookies.jar
    )
    assert (await browser.get(hosted_app.origin + "/")).status_code == 200


async def test_private_missing_document_navigation_offers_workspace_recovery(
    browser, hosted_app, authenticated_client
):
    await establish(browser, authenticated_client, hosted_app.origin)
    url = hosted_app.origin + "/library/report.pdf"
    navigation = await browser.get(url, headers={"Accept": "text/html"})
    assert navigation.status_code == 404
    assert "text/html" in navigation.headers["content-type"], navigation.text
    assert (
        hosted_app.pod_id in navigation.text and "library/report.pdf" in navigation.text
    )
    assert "Open it in your workspace" in navigation.text
    assert navigation.headers["cache-control"] == "private, no-store"
    asset = await browser.get(url, headers={"Accept": "application/pdf"})
    assert (
        asset.status_code == 404 and "application/json" in asset.headers["content-type"]
    )


@pytest.mark.parametrize(
    "attack", ["verifier", "binding", "other-binding", "cross-app", "origin", "replay"]
)
async def test_redemption_is_bound_and_single_use(
    browser, hosted_app, authenticated_client, attack
):
    origin = hosted_app.origin
    pending = await offer(browser, origin)
    approved = await approve(authenticated_client, pending, origin)
    assert approved.status_code == 200, approved.text
    data = {
        "request_id": pending,
        "code": approved.json()["code"],
        "verifier": VERIFIER,
    }
    target = origin
    headers = {"Origin": origin}
    if attack == "verifier":
        data["verifier"] = "x" * 64
    elif attack == "binding":
        browser.cookies.clear()
    elif attack == "other-binding":
        other = await offer(browser, origin)
        other_binding = browser.cookies[f"__Host-lemmaAppAccessBinding-{other}"]
        headers["Cookie"] = f"__Host-lemmaAppAccessBinding-{pending}={other_binding}"
    elif attack == "cross-app":
        target = "https://other.apps.example.test"
        headers["Origin"] = target
    elif attack == "origin":
        headers["Origin"] = "https://other.example.test"
    elif attack == "replay":
        first = await browser.post(
            target + "/_lemma/app-access/redeem", headers=headers, json=data
        )
        assert first.status_code == 200
    refused = await browser.post(
        target + "/_lemma/app-access/redeem", headers=headers, json=data
    )
    assert (
        refused.status_code == 401
        and "__Host-lemmaAppAccess=" not in refused.headers.get("set-cookie", "")
    )


async def test_logout_and_account_disable_block_existing_access(
    browser, hosted_app, authenticated_client
):
    token = await establish(browser, authenticated_client, hosted_app.origin)
    store = AppAccessStore()
    access = await store.get_session(token, origin=hosted_app.origin)
    from app.core.infrastructure.db.session import async_session_maker

    async with async_session_maker() as session:
        await session.execute(
            update(User).where(User.id == access.user_id).values(is_active=False)
        )
        await session.commit()
    try:
        disabled = await browser.get(hosted_app.origin + "/assets/app.js")
        assert disabled.status_code == 401
    finally:
        async with async_session_maker() as session:
            await session.execute(
                update(User).where(User.id == access.user_id).values(is_active=True)
            )
            await session.commit()
    assert (await browser.get(hosted_app.origin + "/")).status_code == 200
    await revoke_session(access.parent_handle)
    logged_out = await browser.get(hosted_app.origin + "/assets/app.js")
    assert (
        logged_out.status_code == 401 and "PRIVATE_APP_CONTENT" not in logged_out.text
    )


async def test_public_to_private_stops_anonymous_and_conditional_reads(
    browser, hosted_app, authenticated_client
):
    path = f"/pods/{hosted_app.pod_id}/apps/{hosted_app.name}"
    public = await authenticated_client.patch(path, json={"visibility": "PUBLIC"})
    assert public.status_code == 200, public.text
    opened = await browser.get(hosted_app.origin + "/assets/app.js")
    assert opened.status_code == 200 and "public" in opened.headers["cache-control"]
    etag = opened.headers["etag"]
    assert (
        await browser.get(
            hosted_app.origin + "/assets/app.js", headers={"If-None-Match": etag}
        )
    ).status_code == 304
    private = await authenticated_client.patch(path, json={"visibility": "POD"})
    assert private.status_code == 200
    closed = await browser.get(
        hosted_app.origin + "/assets/app.js", headers={"If-None-Match": etag}
    )
    assert (
        closed.status_code == 401
        and closed.headers["cache-control"] == "private, no-store"
    )
    await establish(browser, authenticated_client, hosted_app.origin)
    assert (
        await browser.get(
            hosted_app.origin + "/assets/app.js", headers={"If-None-Match": etag}
        )
    ).status_code == 200


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
    path = f"/pods/{ctx['pod_id']}/apps/{name}"
    app = await authenticated_client.post(
        f"/pods/{ctx['pod_id']}/apps",
        json={"name": name, "public_slug": name, "visibility": "RESTRICTED"},
    )
    assert app.status_code == 201, app.text
    uploaded = await authenticated_client.post(
        path + "/bundle",
        files={
            "dist_archive": (
                "dist.zip",
                build_dist_archive("PRIVATE_APP_CONTENT"),
                "application/zip",
            )
        },
    )
    assert uploaded.status_code == 200, uploaded.text
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
    pending = await offer(browser, preview)
    denied = await approve(async_client, pending, preview, ctx["custom_headers"])
    assert denied.status_code == 404, denied.text
    await establish(browser, authenticated_client, preview)
    assert (await browser.get(preview + "/")).status_code == 200
    revoked = await authenticated_client.put(grants_path, json={"grants": []})
    assert revoked.status_code == 200, revoked.text
    after = await browser.get(origin + "/assets/app.js")
    assert after.status_code == 404 and "PRIVATE_APP_CONTENT" not in after.text


@pytest.mark.parametrize("expire", ["request", "code", "parent"])
async def test_expired_handoff_or_parent_is_refused(test_app, expire):
    store = AppAccessStore()
    binding = "b" * 43
    request = AppAccessRequest(
        origin="https://orders.apps.example.test",
        slug="orders",
        release_ref=None,
        challenge=CHALLENGE,
        binding_hash=token_hash(binding),
    )
    session = AppAccessSession(
        app_id=uuid4(),
        pod_id=uuid4(),
        name="orders",
        user_id=uuid4(),
        origin=request.origin,
        slug="orders",
        release_ref=None,
        parent_handle="parent",
        expires_at=int(time.time()) + 300,
    )
    pending = await store.create(request, client_key=uuid4().hex)
    code = await store.authorize(pending, request, session)
    redis = get_redis()
    if expire == "parent":
        session.expires_at = int(time.time()) - 1
        from app.modules.apps.domain.access import AppAccessCode

        await redis.set(
            _key("code", code),
            AppAccessCode(request_id=pending, session=session).model_dump_json(),
            ex=60,
        )
    else:
        await redis.expire(_key(expire, pending if expire == "request" else code), 0)
    with pytest.raises(AppAccessInvalidError):
        await store.redeem(
            request_id=pending,
            code=code,
            challenge=CHALLENGE,
            binding=binding,
            origin=request.origin,
        )


async def test_atomic_redemption_and_hashed_session_storage(test_app):
    store = AppAccessStore()
    binding = "b" * 43
    request = AppAccessRequest(
        origin="https://orders.apps.example.test",
        slug="orders",
        release_ref=None,
        challenge=CHALLENGE,
        binding_hash=token_hash(binding),
    )
    session = AppAccessSession(
        app_id=uuid4(),
        pod_id=uuid4(),
        name="orders",
        user_id=uuid4(),
        origin=request.origin,
        slug="orders",
        release_ref=None,
        parent_handle="parent",
        expires_at=int(time.time()) + 90,
    )
    pending = await store.create(request, client_key=uuid4().hex)
    assert 0 < await store.redis.ttl(_key("request", pending)) <= 300
    code = await store.authorize(pending, request, session)
    assert 0 < await store.redis.ttl(_key("code", code)) <= 60
    results = await asyncio.gather(
        *(
            store.redeem(
                request_id=pending,
                code=code,
                challenge=CHALLENGE,
                binding=binding,
                origin=request.origin,
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    successes = [result for result in results if isinstance(result, tuple)]
    assert (
        len(successes) == 1
        and sum(isinstance(result, AppAccessInvalidError) for result in results) == 1
    )
    token, record = successes[0]
    assert record == session
    assert await store.redis.get("apps:access:session:" + token) is None
    assert 0 < await store.redis.ttl(_key("session", token)) <= 90
    assert (await store.get_session(token, origin=request.origin)) == session
    with pytest.raises(AppAccessInvalidError):
        await store.get_session(token, origin="https://other.apps.example.test")
    client_key = uuid4().hex
    monkey_limit = apps_settings.app_access_create_limit_per_minute
    try:
        apps_settings.app_access_create_limit_per_minute = 1
        await store.create(request, client_key=client_key)
        with pytest.raises(AppAccessRateLimitedError):
            await store.create(request, client_key=client_key)
    finally:
        apps_settings.app_access_create_limit_per_minute = monkey_limit


async def test_redis_outage_is_a_private_bounded_error(browser, hosted_app, test_app):
    from app.modules.apps.api.controllers.app_access_controller import (
        get_app_access_store,
    )
    import socket

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    redis = Redis(
        host="127.0.0.1", port=port, socket_connect_timeout=0.1, socket_timeout=0.1
    )
    test_app.dependency_overrides[get_app_access_store] = lambda: AppAccessStore(redis)
    try:
        response = await browser.post(
            hosted_app.origin + "/_lemma/app-access/requests",
            headers={"Origin": hosted_app.origin},
            json={"challenge": CHALLENGE},
        )
    finally:
        test_app.dependency_overrides.pop(get_app_access_store)
        await redis.aclose()
    assert (
        response.status_code == 503
        and response.headers["cache-control"] == "private, no-store"
    )
    assert "provider detail" not in response.text


async def test_identity_outage_cannot_serve_private_content(
    browser, hosted_app, authenticated_client, monkeypatch
):
    from supertokens_python.recipe.session.recipe import SessionRecipe
    from supertokens_python.exceptions import SuperTokensError
    from unittest.mock import AsyncMock

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
    assert (
        "provider secret" not in failed.text
        and "PRIVATE_APP_CONTENT" not in failed.text
    )


async def test_handoff_paths_do_not_expose_the_general_api(
    browser, hosted_app, authenticated_client
):
    refused = await browser.post(
        "https://api.example.test/_lemma/app-access/requests",
        headers={"Origin": hosted_app.origin, "X-App-Public-Slug": hosted_app.name},
        json={"challenge": CHALLENGE},
    )
    assert refused.status_code == 401
    created = await browser.post(
        hosted_app.origin + "/public/apps/_lemma/app-access/requests",
        headers={"Origin": hosted_app.origin, "X-App-Public-Slug": "forged"},
        json={"challenge": CHALLENGE},
    )
    assert created.status_code == 200
    pending = created.json()["request_id"]
    wrong = await authenticated_client.post(
        f"/apps/access/requests/{pending}/authorize",
        headers={"Origin": "https://other.example.test"},
        json={"app_origin": hosted_app.origin},
    )
    assert wrong.status_code == 401
    assert (
        await browser.post(hosted_app.origin + "/_lemma/users/me")
    ).status_code != 200
    unauthenticated = await browser.post(
        f"/apps/access/requests/{pending}/authorize",
        json={"app_origin": hosted_app.origin},
    )
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("parent_state", ["expired", "wrong-user", "missing"])
async def test_parent_session_information_is_revalidated(
    browser, hosted_app, authenticated_client, monkeypatch, parent_state
):
    from supertokens_python.recipe.session.recipe import SessionRecipe
    from unittest.mock import AsyncMock

    token = await establish(browser, authenticated_client, hosted_app.origin)
    access = await AppAccessStore().get_session(token, origin=hosted_app.origin)
    implementation = SessionRecipe.get_instance().recipe_implementation
    information = await implementation.get_session_information(access.parent_handle, {})
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
    refused = await browser.get(hosted_app.origin + "/assets/app.js")
    assert refused.status_code == 401 and "PRIVATE_APP_CONTENT" not in refused.text
