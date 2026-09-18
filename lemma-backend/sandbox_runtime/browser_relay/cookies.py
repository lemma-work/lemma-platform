"""Reading which sites the browser is signed in to, and forgetting one.

The replacement for `state.py`, and much less than it was. That file lifted a
whole browser session out through the CLI so the backend could encrypt it and
put it back later. Nothing is lifted out now -- the profile is durable, so the
session simply stays -- and what is left is the two things a person needs in
order to see and undo what their browser is holding.

**No cookie value ever leaves the sandbox.** `list_cookie_domains` returns the
host a cookie was set for and when it lapses, and nothing else. That is enough
to say "you are signed in to example.com" and enough to delete it, and it is
deliberately not enough to sign in as them somewhere else. The old capture had
to carry values by construction; this does not, so it does not.

**The relay decides nothing about sites.** It reports hosts and deletes the
hosts it is given. Whether `api.example.com` and `example.com` are one login
is a public-suffix question, and the public-suffix list lives in the backend
-- the same split `state.py` documented, for the same reason: a policy that
can be answered in two places is a policy that will be answered differently.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import websockets

from .chrome import BrowserNotRunning

#: Chrome answers a CDP call in well under this unless it is wedged, in which
#: case waiting longer only holds the request open.
_CDP_TIMEOUT_SECONDS = 15.0

#: The fields `Storage.setCookies` accepts back. `getCookies` returns more --
#: `size`, `session`, `priority`, `sourcePort` -- and handing those back is
#: rejected, which matters because rewriting the survivors is how a single
#: site is forgotten.
_SETTABLE = (
    "name",
    "value",
    "domain",
    "path",
    "expires",
    "httpOnly",
    "secure",
    "sameSite",
)


async def _browser_socket(port: int) -> str:
    """The browser-level CDP endpoint, which is not a page's.

    Cookies are the browser's, not any one tab's: asking a page would miss
    every site with no tab open, which after an idle retirement is all of
    them.
    """
    try:
        async with httpx.AsyncClient(timeout=_CDP_TIMEOUT_SECONDS) as client:
            response = await client.get(f"http://127.0.0.1:{port}/json/version")
            response.raise_for_status()
            url = str(response.json().get("webSocketDebuggerUrl") or "")
    except (httpx.HTTPError, ValueError) as exc:
        raise BrowserNotRunning("the browser is not running") from exc
    if not url:
        raise BrowserNotRunning("the browser is not running")
    return url


async def _call(socket, method: str, params: dict[str, Any] | None = None) -> dict:
    """One CDP request/response over an open socket.

    Ids are per-connection and every call here is sequential, so a counter
    would be ceremony: each send is awaited to its own reply before the next.
    """
    await socket.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
    while True:
        message = json.loads(await socket.recv())
        # Events share the socket and have no `id`; skip them rather than
        # mistaking the first one that arrives for the answer.
        if message.get("id") != 1:
            continue
        if "error" in message:
            raise BrowserNotRunning(str(message["error"].get("message", method)))
        return message.get("result") or {}


async def list_cookie_domains(*, port: int) -> list[dict[str, Any]]:
    """Every cookie the browser holds, as host and expiry only.

    Sorted nowhere and grouped nowhere: the caller owns both, because the
    caller is what knows about public suffixes.
    """
    async with websockets.connect(
        await _browser_socket(port), open_timeout=_CDP_TIMEOUT_SECONDS
    ) as socket:
        result = await _call(socket, "Storage.getCookies")
    return [
        {
            "domain": str(cookie.get("domain") or "").lstrip("."),
            # CDP gives -1 for a session cookie, which is not a time and must
            # not be rendered as one.
            "expires": (
                float(cookie["expires"])
                if isinstance(cookie.get("expires"), (int, float))
                and cookie["expires"] > 0
                else None
            ),
        }
        for cookie in result.get("cookies") or []
        if cookie.get("domain")
    ]


async def forget_domains(domains: list[str], *, port: int) -> int:
    """Drop every cookie set for one of these hosts, and keep the rest.

    Read, filter, clear, write back. `Storage.clearCookies` is all-or-nothing
    and `Network.deleteCookies` is a page-level call that would miss any site
    without a tab open, so the survivors are rewritten instead. The window
    where the browser holds no cookies is between two awaits on one socket,
    and the alternative -- leaving a site's session in place while telling the
    person it is gone -- is worse than that window.

    Returns how many were dropped, so a caller can tell "removed" from "there
    was nothing there".
    """
    wanted = {d.lstrip(".").lower() for d in domains if d}
    if not wanted:
        return 0
    async with websockets.connect(
        await _browser_socket(port), open_timeout=_CDP_TIMEOUT_SECONDS
    ) as socket:
        result = await _call(socket, "Storage.getCookies")
        cookies = result.get("cookies") or []
        keep = [
            cookie
            for cookie in cookies
            if str(cookie.get("domain") or "").lstrip(".").lower() not in wanted
        ]
        dropped = len(cookies) - len(keep)
        if not dropped:
            return 0
        await _call(socket, "Storage.clearCookies")
        if keep:
            await _call(
                socket,
                "Storage.setCookies",
                {
                    "cookies": [
                        {k: v for k, v in cookie.items() if k in _SETTABLE}
                        for cookie in keep
                    ]
                },
            )
    return dropped


__all__ = ["forget_domains", "list_cookie_domains"]
