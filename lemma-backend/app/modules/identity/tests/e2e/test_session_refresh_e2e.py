"""What a browser session has to survive to stay usable.

A desktop install spent days answering 401 to every authorized route while
`/st/auth/session/refresh` answered 200 to every refresh. The frontend SDK did
exactly what it is supposed to -- refresh, retry, refresh, retry -- and the app
sat signed in and unable to do anything, including sign out.

Nothing in the suite covered the join: there were tests that a cookie session
authorizes `/users/me`, and tests that refresh returns 200, and none that the
session *after* a refresh still authorizes anything. These are that join.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.modules.test_support.e2e_base import verify_emailpassword_for_tests

pytestmark = pytest.mark.e2e


def _credentials(email: str, password: str = "TestPassword@123") -> dict:
    return {
        "formFields": [
            {"id": "email", "value": email},
            {"id": "password", "value": password},
        ]
    }


@asynccontextmanager
async def signed_in_browser(test_app, async_client: AsyncClient):
    """A client holding a cookie session, the way a browser does."""
    email = f"session-refresh-{uuid4().hex[:8]}@example.com"
    signup = await async_client.post("/st/auth/signup", json=_credentials(email))
    assert signup.status_code == 200, signup.text
    await verify_emailpassword_for_tests(signup.json()["user"]["id"], email)

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as browser:
        signin = await browser.post(
            "/st/auth/signin",
            headers={"st-auth-mode": "cookie"},
            json=_credentials(email),
        )
        assert signin.status_code == 200, signin.text
        yield browser


@pytest.mark.asyncio
async def test_a_refreshed_cookie_session_still_authorizes_the_next_request(
    test_app, async_client: AsyncClient
):
    async with signed_in_browser(test_app, async_client) as browser:
        assert (await browser.get("/users/me")).status_code == 200

        refresh = await browser.post("/st/auth/session/refresh")
        assert refresh.status_code == 200, refresh.text

        # The whole failure, in one assertion: a 401 here is the state the
        # frontend answers by refreshing again, forever.
        after = await browser.get("/users/me")
        assert after.status_code == 200, after.text


@pytest.mark.asyncio
async def test_refreshing_repeatedly_does_not_wear_the_session_out(
    test_app, async_client: AsyncClient
):
    """Refresh is on the hot path of every long-lived tab, not a rare event."""
    async with signed_in_browser(test_app, async_client) as browser:
        for attempt in range(3):
            refresh = await browser.post("/st/auth/session/refresh")
            assert refresh.status_code == 200, f"refresh {attempt}: {refresh.text}"
            authorized = await browser.get("/users/me")
            assert authorized.status_code == 200, (
                f"after refresh {attempt}: {authorized.text}"
            )


@pytest.mark.asyncio
async def test_refresh_replaces_the_access_token_the_browser_holds(
    test_app, async_client: AsyncClient
):
    """A refresh the browser does not store is a refresh that fixes nothing.

    The 200 is not the contract; the new token in the jar is. An install that
    kept presenting the token it already had would look exactly like a healthy
    refresh from the server's side.
    """
    async with signed_in_browser(test_app, async_client) as browser:
        before = browser.cookies.get("sAccessToken")
        assert before

        assert (await browser.post("/st/auth/session/refresh")).status_code == 200

        after = browser.cookies.get("sAccessToken")
        assert after and after != before


@pytest.mark.asyncio
async def test_signing_out_ends_a_session_the_browser_can_still_present(
    test_app, async_client: AsyncClient
):
    """Sign-out is an authorized call, so it has to work while one still works.

    The desktop failure made this the trap it is: by the time a user wants out,
    the session may already be one the server rejects -- at which point the
    only escape is client-side, which is why the frontend no longer relies on
    this call succeeding.
    """
    async with signed_in_browser(test_app, async_client) as browser:
        assert (await browser.post("/st/auth/signout")).status_code == 200
        assert (await browser.get("/users/me")).status_code == 401
