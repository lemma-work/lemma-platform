"""Clearing a site's cookies, and when it must refuse to say it did.

The whole feature exists because the version before it reported success for
work it had not done. These are the two ways that could come back.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException, status

from app.modules.web_login.api.controllers import web_login_controller as controller


class _Browser:
    def __init__(self, *, running: bool, cookies: list[dict] | None = None) -> None:
        self._answer = {"running": running, "cookies": cookies or [], "signed_in": []}
        self.forgot: list[tuple[list[str], list[str]]] = []

    async def signed_in_sites(self, _user_id, *, wake: bool = False):
        return self._answer

    async def forget_sites(self, _user_id, *, domains, sites):
        self.forgot.append((domains, sites))
        return len(domains)


class _User:
    id = uuid4()


def browser(**kwargs) -> _Browser:
    """The collaborator, handed over rather than patched in.

    The route takes its browser as a dependency, so a test hands one in the
    same way FastAPI does. Patching the controller's own factory would be a
    double inside the unit under test: it certifies the half nobody wrote,
    and it stays green through a rename that should have failed.
    """
    return _Browser(**kwargs)


@pytest.mark.asyncio
async def test_a_retired_browser_refuses_rather_than_reporting_nothing() -> None:
    """An awake sandbox whose Chrome has retired for idleness.

    No exception is raised -- the relay answers, it just says the browser is
    down -- so this used to fall through to `forgotten: false` with a 200.
    The screen reads a successful mutation as "cleared", while the session
    sits untouched on the durable disk. Cookies are read and cleared over
    CDP; a browser that is not running cannot be changed.
    """
    with pytest.raises(HTTPException) as raised:
        await controller.forget_web_login(
            _User(), browser=browser(running=False), origin="https://app.example.com"
        )

    assert raised.value.status_code == status.HTTP_409_CONFLICT


@pytest.mark.asyncio
async def test_a_site_the_browser_never_held_is_not_an_error() -> None:
    """Different from the above, and must stay different: the browser is up
    and simply has nothing for this site. Nothing to do, nothing went
    wrong."""
    answer = await controller.forget_web_login(
        _User(),
        browser=browser(
            running=True, cookies=[{"domain": "other.test", "expires": None}]
        ),
        origin="https://app.example.com",
    )

    assert answer.forgotten is False


@pytest.mark.asyncio
async def test_a_running_browser_clears_the_hosts_of_that_site() -> None:
    made = browser(
        running=True,
        cookies=[
            {"domain": "app.example.com", "expires": None},
            {"domain": "api.example.com", "expires": None},
            {"domain": "unrelated.test", "expires": None},
        ],
    )

    answer = await controller.forget_web_login(
        _User(), browser=made, origin="https://app.example.com"
    )

    assert answer.forgotten is True
    ((domains, sites),) = made.forgot
    assert sorted(domains) == ["api.example.com", "app.example.com"]
    assert sites == ["example.com"]
