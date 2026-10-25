"""How a browser-view socket ends: refused, hung up on, and tidied after.

Its own module because they answer one question together -- how to close a
socket so the person's browser hears the reason -- which the routes only need
to call, and because the socket controller is long enough without them.
"""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import WebSocket, WebSocketDisconnect

from app.core.log.log import get_logger

logger = get_logger(__name__)


#: What closing a socket that is already over can raise.
#:
#: `RuntimeError` is starlette's, for a socket in the wrong state, and it was
#: the obvious guess and the only one handled. The one production actually
#: threw is `AttributeError`, from inside uvicorn's own close path
#: (`'WebSocketProtocol' object has no attribute 'transfer_data_task'`) when the
#: handshake never completed -- so a refusal aimed at a client that had already
#: gone became an unhandled ASGI error, and the pane, seeing an error rather
#: than its close code, retried. `OSError` covers the transport being gone
#: underneath, `ConnectionError` included.
#:
#: `WebSocketDisconnect` is starlette's for a client that has already gone, and
#: is the *ordinary* case here rather than an edge: by the time anything is
#: being refused, the person may well have navigated away.
#:
#: This tuple has now been corrected twice from production, which is the honest
#: note to leave. It began as `RuntimeError` alone; `AttributeError` was found
#: crashing refusals in dev; `WebSocketDisconnect` was found crashing them again
#: in the local E2B run that was meant to confirm the first fix. So read the
#: list as "the ways a socket is observed to end", not as a proof of
#: completeness -- and if a fifth appears, the log line below names its type,
#: which is the whole reason it logs rather than swallowing.
_HANGUP_FAILURES = (RuntimeError, AttributeError, OSError, WebSocketDisconnect)


async def collect(task: "asyncio.Task[None]") -> None:
    """Cancel a task and wait for it to finish, without that becoming an error.

    Awaited rather than merely cancelled, because a cancelled task is not
    finished until it has been collected and leaving it uncollected is how a
    task outlives the request that started it.

    `CancelledError` by name, and that is the whole point. Cancelling is what
    makes awaiting it raise, and `CancelledError` is a `BaseException` -- so the
    `suppress(Exception)` this replaces caught everything *except* the one
    exception the line is guaranteed to produce. uvicorn logged "Exception in
    ASGI application" on every close of the browser pane: a stack trace for the
    ordinary act of stopping watching.
    """
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def hang_up(websocket: WebSocket, code: int, *, doing: str) -> None:
    """Accept if needed, then close, and never raise while doing it.

    Broad on purpose, and logged rather than swallowed. `RuntimeError` alone was
    the obvious guess and the wrong one: a socket whose handshake never
    completed raises `AttributeError` from inside uvicorn's own close path
    (`'WebSocketProtocol' object has no attribute 'transfer_data_task'`), so a
    refusal aimed at a client that had already gone became an unhandled ASGI
    error -- and the pane, which sees an error rather than its close code,
    retries. "The browser is not running" is the ordinary resting state of an
    idle workspace, and it was reaching people as a crash loop.

    Whatever goes wrong here, the caller has already decided this socket is
    over. There is nothing left to fail into, which is what makes catching
    everything the right shape rather than a shrug.
    """
    try:
        await websocket.accept()
    except _HANGUP_FAILURES as exc:
        # Already accepted is the ordinary case and not worth a line.
        logger.debug(
            "workspace.browser_view.accept_before_close_failed.observed",
            doing=doing,
            error_type=type(exc).__name__,
        )
    try:
        await websocket.close(code=code)
    except _HANGUP_FAILURES as exc:
        logger.debug(
            "workspace.browser_view.close_not_delivered.observed",
            doing=doing,
            close_code=code,
            error_type=type(exc).__name__,
        )


async def refuse(websocket: WebSocket, code: int) -> None:
    """Close with a code the person's browser will actually receive.

    A close sent *before* `accept()` is not a close. ASGI turns it into a
    rejected handshake -- uvicorn answers HTTP 403 -- and a rejected handshake
    reaches page script as `code: 1006`, the anonymous "abnormal closure" a
    browser reports when it never had a connection at all. The close code is
    part of the WebSocket close *frame*, and there is no frame until the socket
    has been accepted.

    So every one of the five codes below arrived at the pane as the same 1006,
    and two things followed from it. The person was told "The connection
    dropped. Reconnecting." no matter what had really happened -- including
    "the browser is not running", which is the ordinary resting state of an
    idle workspace and not a failure at all. And the view retried, for ever,
    because 1006 is the code it is right to retry: the client has explicit
    logic to stop on a refusal that will never become an acceptance, and that
    logic could never fire.

    Accepting a socket in order to close it is backwards, and the reason it is
    correct anyway is that nothing is sent between the two. A caller refused
    here gets an open event, a close frame carrying the reason, and no bytes.
    """
    await hang_up(websocket, code, doing="refusing")


__all__ = ["collect", "hang_up", "refuse"]
