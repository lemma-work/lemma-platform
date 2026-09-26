"""A browser holding two copies of a session cookie must end up holding one.

The refresh requests here go through the real SuperTokens middleware, mounted
the way `app.app` mounts it: the duplicate check runs before SuperTokens asks
its core anything, so the answer being rewritten is the library's own and not
a stand-in for it.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from supertokens_python.framework.fastapi import get_middleware

from app.core.api.session_cookie_scope import RefreshCookieScopeMiddleware
from app.core.config import settings
from app.modules.apps.api.host_routing import AppHostRoutingMiddleware
from app.modules.identity.config import identity_settings
from app.modules.identity.infrastructure.supertokens_auth.duplicate_session_cookies import (
    CookieScope,
    DuplicateSessionCookieMiddleware,
    LiveCookieShape,
    candidate_domains,
    duplicated_session_cookies,
    path_prefixes,
    stray_scopes,
)
from app.modules.identity.infrastructure.supertokens_auth.initialization import (
    initialize_supertokens,
)
from app.modules.test_support.e2e_base import _reset_supertokens_testing_state

pytestmark = pytest.mark.unit

APP_HOST = "demo.apps.lemma.localhost:8711"
WORKSPACE_HOST = "app.lemma.localhost:8711"


def _app() -> FastAPI:
    """The middleware stack of `app.app`, reduced to what a refresh crosses."""
    auth = FastAPI()
    auth.add_middleware(get_middleware())

    app = FastAPI()
    app.mount("/st", auth)

    @app.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(RefreshCookieScopeMiddleware)
    app.add_middleware(AppHostRoutingMiddleware)
    app.add_middleware(DuplicateSessionCookieMiddleware)
    return app


def _configure(monkeypatch, *, desktop: bool) -> None:
    monkeypatch.setenv("SUPERTOKENS_ENV", "testing")
    monkeypatch.setattr(settings, "api_url", "http://app.lemma.localhost:8711")
    monkeypatch.setattr(settings, "app_base_domain", "apps.lemma.localhost:8711")
    monkeypatch.setattr(identity_settings, "supertokens_api_base_path", "/auth")
    monkeypatch.setattr(identity_settings, "session_cookie_older_domain", "")
    monkeypatch.setattr(identity_settings, "session_cookie_secure", False)
    monkeypatch.setattr(identity_settings, "session_cookie_same_site", "lax")
    if desktop:
        # What the Desktop host pack renders (`native_host_pack/build.rs`).
        monkeypatch.setattr(
            identity_settings, "session_cookie_domain", ".lemma.localhost"
        )
        monkeypatch.setattr(identity_settings, "supertokens_api_gateway_path", "/st")
        monkeypatch.setattr(settings, "app_api_via_app_origin", True)
    else:
        # What the sharing overlay puts over it (`daemon/environment.rs`):
        # host-only cookies and no app-origin door. The gateway path stays the
        # local one here only because the test app mounts SuperTokens at /st.
        monkeypatch.setattr(identity_settings, "session_cookie_domain", None)
        monkeypatch.setattr(identity_settings, "supertokens_api_gateway_path", "/st")
        monkeypatch.setattr(settings, "app_api_via_app_origin", False)


@pytest.fixture
def desktop(monkeypatch) -> Iterator[FastAPI]:
    _configure(monkeypatch, desktop=True)
    _reset_supertokens_testing_state()
    initialize_supertokens()
    try:
        yield _app()
    finally:
        _reset_supertokens_testing_state()


@pytest.fixture
def sharing(monkeypatch) -> Iterator[FastAPI]:
    _configure(monkeypatch, desktop=False)
    _reset_supertokens_testing_state()
    initialize_supertokens()
    try:
        yield _app()
    finally:
        _reset_supertokens_testing_state()


async def _call(
    app: FastAPI, path: str, *, host: str, cookie: str, origin: str | None = None
) -> httpx.Response:
    headers = {
        "host": host,
        "cookie": cookie,
        "rid": "session",
        "st-auth-mode": "cookie",
        "fdi-version": "4.0",
    }
    if origin:
        headers["origin"] = origin
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url=f"http://{host}"
    ) as client:
        if path == "/ping":
            return await client.get(path, headers=headers)
        return await client.post(path, headers=headers)


def _clears(response: httpx.Response) -> set[tuple[str, str | None, str]]:
    """``(name, domain, path)`` of every session cookie the response deletes."""
    found: set[tuple[str, str | None, str]] = set()
    for header in response.headers.get_list("set-cookie"):
        pieces = [piece.strip() for piece in header.split(";")]
        name, _, value = pieces[0].partition("=")
        if value not in ("", '""') or "1970" not in header:
            continue
        domain: str | None = None
        path = "/"
        for attribute in pieces[1:]:
            key, _, attr = attribute.partition("=")
            if key.lower() == "domain":
                domain = attr.lstrip(".") or None
            elif key.lower() == "path":
                path = attr
        found.add((name, domain, path))
    return found


# -- the pure parts ---------------------------------------------------------


def test_only_a_repeated_session_cookie_counts_as_a_duplicate():
    assert duplicated_session_cookies("sAccessToken=a; sRefreshToken=r") == set()
    assert duplicated_session_cookies("sAccessToken=a; sAccessToken=b") == {
        "sAccessToken"
    }
    assert duplicated_session_cookies("sRefreshToken=a;sRefreshToken=b; x=1; x=2") == {
        "sRefreshToken"
    }


def test_a_stray_can_live_on_the_host_itself_or_any_parent_but_the_bare_tld():
    assert candidate_domains(
        ["demo.apps.lemma.localhost:8711", "http://app.lemma.localhost:9000"]
    ) == [
        None,
        "demo.apps.lemma.localhost",
        "apps.lemma.localhost",
        "lemma.localhost",
        "app.lemma.localhost",
    ]
    # An address has no parent domain, and a bracketed IPv6 host none either.
    assert candidate_domains(["127.0.0.1:8711", "[::1]:8711"]) == [None]


def test_every_path_that_could_have_sent_the_cookie_is_a_candidate():
    assert path_prefixes("/_lemma/st/auth/session/refresh") == [
        "/",
        "/_lemma",
        "/_lemma/st",
        "/_lemma/st/auth",
        "/_lemma/st/auth/session",
        "/_lemma/st/auth/session/refresh",
    ]


def test_the_live_shape_is_never_a_stray():
    shape = LiveCookieShape(
        domain=".lemma.localhost",
        refresh_path="/",
        secure=False,
        same_site="lax",
        known_refresh_paths=("/st/auth/session/refresh",),
    )
    strays = stray_scopes(
        "sRefreshToken",
        shape,
        hosts=[APP_HOST],
        request_path="/_lemma/st/auth/session/refresh",
    )
    assert CookieScope("lemma.localhost", "/") not in strays
    assert CookieScope(None, "/") in strays
    assert CookieScope("lemma.localhost", "/_lemma/st/auth/session/refresh") in strays
    # SuperTokens' own narrow path, where the refresh cookie lived before it
    # was widened -- not a prefix of the app's path, and a stray all the same.
    assert CookieScope("lemma.localhost", "/st/auth/session/refresh") in strays
    # The access token has only ever been written at `/`.
    access = stray_scopes("sAccessToken", shape, hosts=[APP_HOST], request_path="/x/y")
    assert {scope.path for scope in access} == {"/"}
    assert CookieScope("lemma.localhost", "/") not in access


# -- through SuperTokens ----------------------------------------------------


async def test_a_pod_app_refresh_carrying_a_host_only_stray_clears_it_and_keeps_the_live_cookie(
    desktop,
):
    """The case a pod app met on its first load: 200, no front-token, and the
    duplicate still there on the next refresh."""
    response = await _call(
        desktop,
        "/_lemma/st/auth/session/refresh",
        host=APP_HOST,
        cookie="sAccessToken=a1; sRefreshToken=r-live; sRefreshToken=r-stray",
    )

    # SuperTokens' own duplicate answer, unchanged in status and body.
    assert response.status_code == 200
    assert "front-token" not in response.headers
    assert "multiple session cookies" in response.json()["message"]

    clears = _clears(response)
    # The live copy survives: the retry that follows needs it.
    assert ("sRefreshToken", "lemma.localhost", "/") not in clears
    assert ("sAccessToken", "lemma.localhost", "/") not in clears
    # Every other shape the stray can have is cleared -- not only the one
    # `older_cookie_domain` names.
    for expected in (
        ("sRefreshToken", None, "/"),
        ("sRefreshToken", None, "/st/auth/session/refresh"),
        ("sRefreshToken", "lemma.localhost", "/st/auth/session/refresh"),
        ("sRefreshToken", "lemma.localhost", "/_lemma/st/auth/session/refresh"),
        ("sRefreshToken", "apps.lemma.localhost", "/"),
        ("sRefreshToken", "demo.apps.lemma.localhost", "/"),
        ("sAccessToken", None, "/"),
        ("sAccessToken", "apps.lemma.localhost", "/"),
    ):
        assert expected in clears, expected


async def test_behind_the_macos_alias_the_page_hosts_strays_are_named_too(desktop):
    # locald rewrites Host to the canonical app host; the page is on the
    # workspace host, whose host-only cookies are the ones it carries.
    response = await _call(
        desktop,
        "/_lemma/st/auth/session/refresh",
        host=APP_HOST,
        origin="http://app.lemma.localhost:52001",
        cookie="sAccessToken=a1; sAccessToken=a2; sRefreshToken=r1",
    )
    assert response.status_code == 200
    clears = _clears(response)
    assert ("sAccessToken", "app.lemma.localhost", "/") in clears
    assert ("sAccessToken", None, "/") in clears
    assert ("sAccessToken", "lemma.localhost", "/") not in clears


async def test_while_sharing_the_live_host_only_cookie_is_not_the_one_cleared(sharing):
    """With the domain blank, SuperTokens' "older" domain is the live one.

    Left alone it deletes the session being used and keeps the stale
    `Domain=lemma.localhost` copy from before sharing was turned on."""
    response = await _call(
        sharing,
        "/st/auth/session/refresh",
        host=WORKSPACE_HOST,
        cookie="sAccessToken=a-live; sAccessToken=a-stale; sRefreshToken=r1; sRefreshToken=r2",
    )
    assert response.status_code == 200
    assert "front-token" not in response.headers
    clears = _clears(response)
    assert ("sAccessToken", None, "/") not in clears
    assert ("sRefreshToken", None, "/st/auth/session/refresh") not in clears
    assert ("sAccessToken", "lemma.localhost", "/") in clears
    assert ("sRefreshToken", "lemma.localhost", "/") in clears
    assert ("sRefreshToken", "lemma.localhost", "/st/auth/session/refresh") in clears


async def test_any_request_carrying_a_duplicate_clears_the_strays(desktop):
    """Toggling sharing leaves the pair behind; the first API call after it
    removes the stray instead of waiting for a refresh to trip over it."""
    response = await _call(
        desktop, "/ping", host=WORKSPACE_HOST, cookie="sAccessToken=a1; sAccessToken=a2"
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    clears = _clears(response)
    assert ("sAccessToken", None, "/") in clears
    assert ("sAccessToken", "lemma.localhost", "/") not in clears


async def test_a_request_without_duplicates_gets_no_extra_headers(desktop):
    response = await _call(
        desktop, "/ping", host=WORKSPACE_HOST, cookie="sAccessToken=a1"
    )
    assert response.headers.get_list("set-cookie") == []
