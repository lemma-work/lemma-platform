"""Driving the workspace browser from a page, not just watching it.

The dashboard `agent-browser` ships streams viewports and has no input path, so
watching is all it can offer. Taking the wheel needs Chrome's own protocol, and
this is the bridge to it: the viewer's browser talks to us, we talk to the
sandbox runtime's filtered relay, and the runtime talks to Chrome.

**Three hops on purpose.** The debugging protocol never reaches the viewer's
browser directly. Only the runtime's own port is published, so CDP is reachable
exclusively through it — and that turns out to be where the safety lives too:
the runtime passes input and screencast and refuses everything else, so a page
holding this socket cannot read the session's cookies, evaluate script in it, or
navigate it somewhere that harvests it. See
``sandbox_runtime/workspace/app.py``'s ``cdp_message_is_allowed``.

The websocket authenticates its own handshake, the way the datastore changes
socket does, because the global HTTP auth dependency cannot see an upgrade.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from collections.abc import Awaitable, Callable
from typing import Annotated, TypeAlias
from uuid import UUID

import httpx
import websockets
from fastapi import APIRouter, Depends, WebSocket
from sandbox_runtime.errors import SandboxCapabilityUnsupported
from starlette.websockets import WebSocketDisconnect
from supertokens_python.exceptions import SuperTokensError
from supertokens_python.recipe.session.asyncio import (
    get_session_without_request_response,
)

from app.core.api.dependencies import CurrentUser
from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/workspace/apps/browser", tags=["Workspace Apps"])

#: Path the security layer allowlists, kept here so the two stay in step.
BROWSER_STREAM_WS_SUFFIX = "/workspace/apps/browser/stream"


def get_workspace_service() -> WorkspaceSandboxService:
    return WorkspaceSandboxService()


WorkspaceServiceDep = Annotated[WorkspaceSandboxService, Depends(get_workspace_service)]


@router.get(
    "/targets",
    operation_id="workspace.browser.targets",
    summary="List pages the workspace browser has open",
)
async def list_browser_targets(
    user: CurrentUser, service: WorkspaceServiceDep
) -> dict[str, list[dict[str, str]]]:
    """What there is to watch.

    An empty list when the browser is not running, rather than an error: a
    workspace whose browser has been shed for idleness or memory is the ordinary
    resting state, and the caller wants "nothing yet".
    """
    try:
        base_url, headers = await _endpoint(service, user.id)
        targets = await _targets(base_url, headers)
    except SandboxCapabilityUnsupported:
        # This provider does not offer a drivable browser. Saying "none" is
        # honest: there is nothing here to watch.
        return {"targets": []}
    finally:
        await service.close()
    return {"targets": [dict(target) for target in targets]}


async def _endpoint(
    service: WorkspaceSandboxService, user_id: UUID
) -> tuple[str, dict[str, str]]:
    """The runtime's address and credential for this person's workspace."""
    session = await service.get_session(
        user_id, pod_id=None, initial_cwd="/workspace", close_on_exit=False
    )
    async with session:
        return await session.client.browser_cdp_endpoint(
            session.logical_id,
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=20),
        )


async def _targets(
    base_url: str, headers: dict[str, str], *, start: bool = False
) -> tuple[dict[str, str], ...]:
    """The pages Chrome has open, or none when it is not running.

    A 409 means the browser has been shed for idleness or memory, which is the
    ordinary resting state rather than a failure.

    `start` asks the runtime to bring a browser up if none is running. Only the
    stream passes it: somebody opening a takeover has asked to drive, and
    reporting "nothing to watch" at them is the failure they actually hit. A
    card rendering in a transcript has asked for no such thing, and starting a
    browser costs tens of seconds and a few hundred megabytes in a sandbox where
    220 MB free already triggers a kill.

    The timeout is generous for the same reason, and sits above the runtime's
    own start bound so that a slow cold start is reported by the side that can
    say what happened rather than as a client timeout here.
    """
    async with httpx.AsyncClient(timeout=300.0 if start else 20.0) as client:
        response = await client.get(
            f"{base_url}/browser/cdp/targets",
            headers=headers,
            params={"start": "true"} if start else None,
        )
    if response.status_code == 409:
        return ()
    response.raise_for_status()
    return tuple(response.json().get("targets", ()))


#: Reading a session out of a handshake is the one collaborator this module
#: has that a test cannot supply for real, so it is a parameter.
SessionReader: TypeAlias = Callable[..., Awaitable[object]]


async def resolve_user_id(
    websocket: WebSocket,
    *,
    read_session: SessionReader = get_session_without_request_response,
) -> UUID | None:
    """The signed-in user behind a websocket handshake, or None.

    Same order as the datastore changes socket: bearer, then the session cookie,
    then a query parameter for clients that cannot attach the cookie cross-site.

    Returns a ``UUID``, not the string the session hands back. Everything
    downstream keys a sandbox by it and eventually asks for ``.hex``, so a
    string reaches container naming and fails there — a long way from here, in a
    traceback that says nothing about sessions.
    """
    token: str | None = None
    authorization = websocket.headers.get("authorization") or ""
    scheme, _, raw = authorization.partition(" ")
    if scheme.lower() == "bearer" and raw.strip():
        token = raw.strip()
    if token is None:
        token = (
            websocket.cookies.get("sAccessToken")
            or websocket.cookies.get("st-access-token")
            or websocket.query_params.get("access_token")
        )
    if not token:
        return None
    session = await read_session(token, anti_csrf_check=False, session_required=True)
    if session is None:
        return None
    try:
        return UUID(session.get_user_id())
    except ValueError:
        # A session whose subject is not a Lemma user id is not one this route
        # can serve, and guessing at it would key a sandbox to nothing.
        logger.warning("workspace.browser_stream.user_id_unparsable.degraded")
        return None


@router.websocket("/stream")
async def stream_browser(
    websocket: WebSocket,
    resolve: Callable[[WebSocket], Awaitable[UUID | None]] = resolve_user_id,
) -> None:
    """Carry one page's filtered debugging protocol to a viewer.

    A workspace is keyed by user, so the session is the whole authorization
    check: there is no sandbox parameter, and nobody can name somebody else's
    machine.
    """
    try:
        user_id = await resolve(websocket)
    except SuperTokensError:
        # Expired, revoked, or malformed — all of them mean the same thing here,
        # and none of them is a defect worth a traceback. Anything outside this
        # set is a bug and should surface as one rather than as "not signed in".
        logger.warning("workspace.browser_stream.session_unreadable.degraded")
        user_id = None
    if user_id is None:
        # Refused before accepting, so an unauthenticated caller never holds an
        # open socket onto somebody's browser.
        await websocket.close(code=4401)
        return

    service = WorkspaceSandboxService()
    try:
        base_url, headers = await _endpoint(service, user_id)
        targets = await _targets(base_url, headers, start=True)
        wanted = websocket.query_params.get("target")
        target = next(
            (item for item in targets if item.get("id") == wanted),
            targets[0] if targets else None,
        )
        if target is None:
            # Nothing running is not a failure; say so with a code the client
            # can tell apart from a refusal.
            await websocket.close(code=4409)
            return

        scheme = "wss" if base_url.startswith("https") else "ws"
        url = f"{scheme}://{base_url.split('://', 1)[-1].rstrip('/')}/browser/cdp/{target['id']}"
        await websocket.accept()
        try:
            connection = await websockets.connect(
                url, additional_headers=headers, max_size=None
            )
        except websockets.exceptions.InvalidStatus as exc:
            # The runtime answered, but has no relay to answer *with*. That is a
            # workspace still running an image from before the relay existed,
            # and telling somebody "the connection dropped" sends them to
            # reconnect forever at a thing that will never work.
            if exc.response.status_code == 404:
                logger.warning("workspace.browser_stream.relay_absent.degraded")
                await websocket.close(code=4426)
                return
            raise
        async with connection as upstream:
            await _pump(websocket, upstream)
    except SandboxCapabilityUnsupported:
        # This provider offers no drivable browser at all, which is the same
        # thing to a viewer as there being nothing to watch.
        with suppress(RuntimeError):
            await websocket.close(code=4409)
    except (
        OSError,
        websockets.exceptions.WebSocketException,
        asyncio.TimeoutError,
        httpx.HTTPError,
    ):
        logger.warning("workspace.browser_stream.upstream.degraded", exc_info=True)
        with suppress(RuntimeError):
            await websocket.close(code=1011)
    finally:
        await service.close()


async def _pump(client: WebSocket, upstream) -> None:
    """Copy messages both ways until either end stops.

    Two tasks, because a screencast pushes frames continuously while a viewer
    reading a login page sends nothing for long stretches — one interleaved loop
    would stall the picture behind an input that never comes.

    **Nothing here logs a message body.** Input frames carry what somebody is
    typing, and on this socket that is a password.
    """

    async def to_upstream() -> None:
        while True:
            message = await client.receive()
            if message["type"] == "websocket.disconnect":
                return
            text = message.get("text")
            if text is not None:
                await upstream.send(text)

    async def to_client() -> None:
        async for frame in upstream:
            if isinstance(frame, str):
                await client.send_text(frame)

    tasks = [
        create_inherited_task(to_upstream(), name="workspace.browser_stream.up"),
        create_inherited_task(to_client(), name="workspace.browser_stream.down"),
    ]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            with suppress(
                WebSocketDisconnect,
                websockets.exceptions.ConnectionClosed,
                asyncio.CancelledError,
            ):
                task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
