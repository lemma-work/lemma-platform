"""Carrying a WebSocket between a person's browser and something in a sandbox.

One implementation, used by both the port proxy and the browser view. They had
the same forty lines twice, and the copies had already drifted: one bounded its
frames and the other did not.

Two tasks rather than one loop, because a stream that is only read when the
other side speaks is not a stream. A browser view sends frames continuously
while the viewer sends nothing at all, so interleaving the reads would stall the
picture behind an input that never comes.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit

from fastapi import WebSocket, WebSocketDisconnect

from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task

logger = get_logger(__name__)

#: A frame larger than this is not a screencast frame or a keystroke, it is
#: something wrong. `max_size=None` -- which is what the port proxy carried --
#: means one frame from a process the agent controls is buffered whole in the
#: API's memory, so a sandbox could make the API fall over from inside.
MAX_FRAME_BYTES = 8 * 1024 * 1024

#: Closed because the far end went away, rather than because anyone decided to.
CLOSE_UPSTREAM_GONE = 1011


async def connect_upstream(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    subprotocols: tuple[str, ...] | None = None,
):
    """Open the sandbox-side socket, carrying whatever the fabric's door needs.

    The headers come from `reach_port`, so this stays ignorant of which fabric
    it is on: E2B wants a traffic token, a preview proxy wants its own, Docker
    wants nothing.

    Imported here rather than at module scope: the library is only ever needed
    once a socket is actually being opened, and naming it at the top puts it in
    the import graph of every process that merely registers a route.
    """
    import websockets

    from app.modules.workspace.providers.desktop_tunnel import tunneled_socket

    # A Desktop guest address arrives already connected through the tunnel;
    # anything else is dialled by `websockets` itself.
    tunneled = await tunneled_socket(url)
    return websockets.connect(
        url,
        sock=tunneled,
        additional_headers=dict(headers or {}),
        subprotocols=list(subprotocols) if subprotocols else None,
        max_size=MAX_FRAME_BYTES,
        # A dead peer that never closes leaves a task pinned to a socket that
        # will never speak again; the ping is what notices.
        ping_interval=20,
        ping_timeout=20,
        open_timeout=30,
    )


async def bridge(client: WebSocket, upstream, *, name: str = "workspace") -> None:
    """Copy frames both ways until either end stops, then take both down.

    The cleanup is the part worth reading. Cancelling the pending task is not
    enough on its own -- it has to be awaited, or the socket it holds is closed
    by garbage collection at a time nothing controls, which under load shows up
    as sockets that outlive their request.
    """

    async def to_upstream() -> None:
        while True:
            message = await client.receive()
            if message["type"] == "websocket.disconnect":
                return
            if (text := message.get("text")) is not None:
                await upstream.send(text)
            elif (data := message.get("bytes")) is not None:
                await upstream.send(data)

    import websockets

    async def to_client() -> None:
        async for frame in upstream:
            if isinstance(frame, str):
                await client.send_text(frame)
            else:
                await client.send_bytes(frame)

    # Inherited, not detached: these two carry frames for the connection that
    # spawned them and die with it, so they belong to its operation.
    tasks = [
        create_inherited_task(to_upstream(), name=f"{name}.to_upstream"),
        create_inherited_task(to_client(), name=f"{name}.to_client"),
    ]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            with contextlib.suppress(
                WebSocketDisconnect,
                websockets.exceptions.ConnectionClosed,
                asyncio.CancelledError,
            ):
                task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _origin_of(url: str) -> str | None:
    """`scheme://host[:port]`, which is all an `Origin` header ever carries.

    Configured URLs are not origins. `auth_frontend_url` is a sign-in *page*
    and on at least one deployment reads `https://<host>/auth`, which cannot
    equal any `Origin` a browser sends -- so as a candidate it was dead
    weight that looked like coverage.
    """
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        # Configured rather than sent, so this is a deployment mistake and
        # not an attack -- but a settings typo must not stop the allowlist
        # being built out of the entries that are fine.
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def origins_from(configured: Iterable[str], *urls: str | None) -> tuple[str, ...]:
    """The allowlist, as a function of its inputs and nothing else.

    Separate from the dependency below so a test can hand it a deployment's
    shape directly. Patching `settings` to test this would be a double
    inside the subject -- it would survive a rename of the very settings
    this is about.
    """
    found: list[str] = []
    for candidate in (*configured, *urls):
        origin = _origin_of(str(candidate)) if candidate else None
        if origin and origin not in found:
            found.append(origin)
    return tuple(found)


def origin_is_allowed(
    origin: str | None,
    *,
    allowed: tuple[str, ...],
    pattern: str | None = None,
) -> bool:
    """Whether a WebSocket handshake came from somewhere we serve.

    Browsers do not apply the same-origin policy to WebSockets, and they do send
    cookies on the handshake -- so without this check any page anyone visits can
    open a socket to this API as the signed-in person and, on the browser view,
    watch their screen and type into it. The header cannot be forged by page
    script, which is what makes checking it worth anything.

    A missing Origin is allowed: it is what a non-browser client sends, and the
    CLI and the tests are non-browser clients. A *present* one has to match.

    `pattern` is `cors_origin_regex`, for deployments whose frontends are
    per-tenant and cannot be listed. Anchored at both ends here whatever the
    configured string does, because an unanchored pattern matching anywhere in
    the origin is how `https://evil.test/?x=app.example.com` gets in.
    """
    if origin is None:
        return True
    normalized = origin.rstrip("/").lower()
    if any(normalized == candidate.rstrip("/").lower() for candidate in allowed):
        return True
    if not pattern:
        return False
    try:
        return re.fullmatch(pattern, origin) is not None
    except re.error:
        # A misconfigured pattern refuses rather than admits. The alternative
        # is an allowlist that silently stops narrowing anything.
        logger.warning("workspace.ws_bridge.origin_pattern_invalid.denied")
        return False


def origin_refusal_hint(origin: str | None, *, allowed: tuple[str, ...]) -> str:
    """Enough to diagnose a refusal without logging what a stranger sent.

    The refusal used to log nothing at all, on the sound principle that an
    `Origin` header is attacker-controlled and a newline in it forges a log
    line. The cost was paid in production: six refusals in two minutes, and
    nothing in the record said which origin or how many were configured, so
    the answer had to be reconstructed from deployment config.

    So: the scheme and the *registrable shape* of the host, both drawn from a
    parse rather than the raw string, plus how many origins were on the list.
    No path, no query, no port-scan surface, and nothing that reaches a log
    line unparsed.
    """
    if origin is None:
        return "no origin, allowed"
    # `urlsplit` raises on a malformed authority -- `http://[` is enough,
    # measured -- and this runs *before* authentication, so without this an
    # unauthenticated client could trade a close code for an unhandled ASGI
    # exception by sending one header. An origin we cannot parse is an
    # origin we refuse; it just has nothing to say about itself.
    try:
        parsed = urlsplit(origin)
        scheme = parsed.scheme if parsed.scheme in ("http", "https") else "other"
        host = parsed.hostname or ""
    except ValueError:
        scheme, host = "other", ""
    shape = ".".join(host.rsplit(".", 2)[-2:]) if "." in host else "opaque"
    safe = re.sub(r"[^a-z0-9.-]", "", shape.lower())[:60] or "opaque"
    return f"{scheme}://…{safe} against {len(allowed)} configured"
