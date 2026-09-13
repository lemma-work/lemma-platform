"""What a browser session holds, as it crosses a boundary.

Published from `contracts/` because three modules speak it -- the workspace
that owns the browser, the relay that reads and writes it, and the saved-login
store that keeps a narrowed copy -- and a shape crossing three boundaries as
"some dict" is a shape nobody checks.

It lives here rather than beside the saved-login code because the browser is
the workspace's. Putting it there made the two modules import each other.
"""

from __future__ import annotations

from typing import TypedDict
from urllib.parse import urlparse


class BrowserCookie(TypedDict, total=False):
    """One cookie as Playwright's storage state writes it."""

    name: str
    value: str
    domain: str
    path: str
    expires: float
    httpOnly: bool
    secure: bool
    sameSite: str


class BrowserOrigin(TypedDict, total=False):
    """Local storage for one origin, as the same format writes it."""

    origin: str
    localStorage: list[dict[str, str]]


class BrowserState(TypedDict):
    """What a browser session holds: cookies, and storage per origin.

    Written down rather than left as `dict` because this crosses three
    boundaries -- the relay, the encrypted column, and the browser it goes back
    into -- and at each one "some dict" is the thing nobody checks.
    """

    cookies: list[BrowserCookie]
    origins: list[BrowserOrigin]


def host_of(origin: str) -> str:
    """The bare host of an origin, without any port."""
    parsed = urlparse(origin if "://" in origin else f"https://{origin}")
    return (parsed.hostname or "").lower()


def browser_view_service():
    """This workspace's live browser, for a module that drives one.

    Imported inside the function, not at module scope: naming the class here
    would pull the provider stack into the import graph of everything that
    wants the *shapes* above, and the service imports those shapes back -- a
    cycle at import time, which is only not a crash because this line does not
    run until somebody calls it.
    """
    from app.modules.workspace.services import browser_view_service as module

    return module.BrowserViewService


def browser_unavailable() -> type[Exception]:
    """What a browser call raises when the sandbox cannot answer."""
    from app.modules.workspace.services.browser_relay_client import (
        BrowserRelayUnavailable,
    )

    return BrowserRelayUnavailable


__all__ = [
    "BrowserCookie",
    "BrowserOrigin",
    "BrowserState",
    "browser_unavailable",
    "browser_view_service",
    "host_of",
]
