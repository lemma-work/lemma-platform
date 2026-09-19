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

import asyncio
from contextlib import suppress
import itertools
import json
from typing import Any
from urllib.parse import urlsplit

import httpx
import websockets

from .chrome import BrowserNotRunning, page_targets

#: Chrome answers a CDP call in well under this unless it is wedged, in which
#: case waiting longer only holds the request open.
_CDP_TIMEOUT_SECONDS = 15.0

#: Everything a site can keep a session in.
#:
#: Named rather than `"all"`, which also takes caches and shader stores that
#: have nothing to do with being signed in and cost a site its offline assets.
_STORAGE_TYPES = ",".join(
    (
        "cookies",
        "local_storage",
        "indexeddb",
        "websql",
        "service_workers",
        "cache_storage",
    )
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


#: A per-connection request id. CDP replies carry the id they answer, and
#: several calls now share one socket -- so reusing `1` meant a reply left
#: unread by a failed call was matched by the *next* one, which then reported
#: somebody else's result. Measured as a silent no-op: clearing three origins
#: cleared the first and quietly skipped the rest.
_next_id = itertools.count(1)


async def _call(socket, method: str, params: dict[str, Any] | None = None) -> dict:
    """One CDP request/response over an open socket, bounded.

    The deadline covers the *reply*, which is the part that was unbounded.
    `_CDP_TIMEOUT_SECONDS` bounded the HTTP discovery and the socket open, so
    a Chrome that accepted the connection and then stopped answering left this
    waiting on `recv()` for ever -- outliving the backend's own HTTP timeout
    and leaving the relay holding an operation nobody was waiting for.
    """

    request_id = next(_next_id)

    async def _exchange() -> dict:
        await socket.send(
            json.dumps({"id": request_id, "method": method, "params": params or {}})
        )
        while True:
            message = json.loads(await socket.recv())
            # Events share the socket and have no `id`, and a reply to an
            # earlier call may still be queued; skip both rather than
            # mistaking the first thing that arrives for the answer.
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise BrowserNotRunning(str(message["error"].get("message", method)))
            return message.get("result") or {}

    try:
        return await asyncio.wait_for(_exchange(), timeout=_CDP_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise BrowserNotRunning(f"the browser did not answer {method}") from exc


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


async def _page_socket(port: int) -> str:
    """Any open page, as a CDP endpoint.

    `Storage.clearDataForOrigin` is refused on the browser-level endpoint --
    measured, it answers `Internal error` -- and accepted on a page session,
    where it clears the origin it is *given* rather than the one the page is
    showing. Verified against a site with no tab open at all: cookies and
    local storage both went, and the site asked for a login again.

    So this needs a page, any page, and Chrome always has one while it is
    running: a fresh browser sits on `chrome://newtab/`.
    """
    found = await page_targets(port=port)
    if not found:
        raise BrowserNotRunning("the browser has no page to act through")
    return f"ws://127.0.0.1:{port}/devtools/page/{found[0]['id']}"


async def _open_origins_for(hosts: set[str], *, port: int) -> set[str]:
    """Origins of open pages whose host is one of these, ports included.

    The part the host alone cannot give us: local storage is keyed by full
    origin, and a cookie records no port.
    """
    found: set[str] = set()
    for target in await page_targets(port=port):
        parsed = urlsplit(str(target.get("url") or ""))
        if parsed.scheme in ("http", "https") and parsed.hostname in hosts:
            found.add(f"{parsed.scheme}://{parsed.netloc}")
    return found


async def forget_domains(domains: list[str], *, port: int) -> int:
    """Sign the browser out of these hosts. Returns how many cookies went.

    `Storage.clearDataForOrigin` per host, which is a targeted delete and
    takes everything a site can hold a session in -- cookies, local storage,
    IndexedDB, service workers, cache storage. Two problems go with the
    approach it replaces.

    **It used to clear the whole jar and write the survivors back.** Read,
    filter, `Storage.clearCookies`, `Storage.setCookies`. A failure between
    the third step and the fourth signed the person out of every site they
    had; two of these running at once could restore cookies the other had
    just deleted; and the rewrite dropped `partitionKey`, silently turning
    partitioned cookies into unpartitioned ones.

    **Cookies are what this reliably removes, and the wording says so.**
    `clearDataForOrigin` is asked for the other storage types too, and it
    demonstrably clears them when driven directly -- but through this path it
    did not, repeatably, and I could not isolate why. So the attempt stays,
    because it costs nothing and helps where it lands, and nothing downstream
    claims more than the cookies. The screen says "clear cookies", not "sign
    out of everything". A site keeping its token only in local storage may
    still be signed in afterwards.

    Both schemes are cleared for each host, because a cookie does not record
    the one it was set on and `Secure` only tells us it was https *somewhere*,
    and the origins of any open page on a matching host go in too, since
    local storage is keyed by full origin and a cookie records no port.
    Clearing an origin that holds nothing is free.
    """
    wanted = {d.lstrip(".").lower() for d in domains if d}
    if not wanted:
        return 0
    async with websockets.connect(
        await _browser_socket(port), open_timeout=_CDP_TIMEOUT_SECONDS
    ) as browser:
        before = (await _call(browser, "Storage.getCookies")).get("cookies") or []
    dropped = sum(
        1
        for cookie in before
        if str(cookie.get("domain") or "").lstrip(".").lower() in wanted
    )

    async with websockets.connect(
        await _page_socket(port), open_timeout=_CDP_TIMEOUT_SECONDS
    ) as page:
        for host in sorted(wanted):
            for scheme in ("https", "http"):
                with suppress(BrowserNotRunning):
                    await _call(
                        page,
                        "Storage.clearDataForOrigin",
                        {
                            "origin": f"{scheme}://{host}",
                            "storageTypes": _STORAGE_TYPES,
                        },
                    )
    return dropped


__all__ = ["forget_domains", "list_cookie_domains"]
