"""The browser relay: one door into this sandbox's browser, for the backend.

Why this is a separate process rather than a route on the workspace runtime:
**E2B sandboxes do not run the workspace runtime.** They serve no HTTP inside
the sandbox at all -- exec and files go through the provider's own SDK -- so a
browser channel that lived in the runtime worked on Docker and existed nowhere
else, which is precisely what happened the first time this was built. A small
process baked into the image runs wherever the image runs: Docker, E2B, the
desktop guest, and anything later that can start a container.

Why not have the backend speak CDP directly through a forwarded port: on E2B
every port is a public name, so that would put raw Chrome debugging protocol --
which reads every cookie and evaluates arbitrary script -- behind nothing but a
traffic token. Here the protocol a viewer speaks is four message types, and this
process is what turns them into the handful of CDP calls they correspond to.

The token is read from a file the backend places through the provider's own
secret-delivery path, and re-read on every request so a resumed sandbox can be
handed a fresh one without restarting anything.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import hmac
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket
from pydantic import BaseModel, Field
import websockets

from sandbox_runtime.tasks import create_background_task

from .chrome import (
    BrowserNotRunning,
    is_safe_session,
    CdpConnection,
    ensure_port,
    keepalive,
    live_port,
    open_url,
    page_socket_url,
    page_targets,
)
from .screencast import CONTROL, VIEW, ScreencastSession, pump
from .state import (
    StateOperationFailed,
    clear_session,
    load_session,
    save_session,
    session_for_domain,
)

#: Deliberately not under `/tmp/lemma-browser`, which `quiesce` deletes before a
#: pause: the token has to survive a resume, and the browser profile must not.
TOKEN_PATH = Path(os.environ.get("LEMMA_RELAY_TOKEN_FILE", "/tmp/lemma-relay/token"))

DEFAULT_PORT = int(os.environ.get("LEMMA_BROWSER_RELAY_PORT", "4850"))

#: The session the agent's own browsing uses, and the one a viewer watches when
#: no particular login is named.
DEFAULT_SESSION = os.environ.get("AGENT_BROWSER_SESSION", "workspace")

#: How often to touch the browser while somebody is watching. Comfortably inside
#: agent-browser's two-minute idle timeout, which counts *commands* -- and
#: watching is not one, so without this the browser retires under a person who
#: is reading the page.
_KEEPALIVE_SECONDS = 45.0

#: A CDP frame from Chrome. Bounded so a page cannot make one viewer's socket
#: into this process's memory problem.
_MAX_CDP_FRAME_BYTES = 8 * 1024 * 1024

CLOSE_UNAUTHENTICATED = 4401
CLOSE_NO_BROWSER = 4409
CLOSE_UPSTREAM_GONE = 1011


class EnsureRequest(BaseModel):
    session: str | None = None
    #: The site this is for. Decides which browser session is used, so that a
    #: sign-in happens in the session its capture will later be read from.
    domain: str | None = None
    #: Where the person is meant to end up. Given when a browser is being
    #: started for somebody's arrival: a fresh browser opens blank, and a blank
    #: page under a heading naming a site is how the first version of this
    #: managed to look broken while working correctly.
    origin: str | None = None


class EnsureResponse(BaseModel):
    target_id: str
    url: str
    started: bool


class StateSaveRequest(BaseModel):
    domain: str | None = None
    session: str | None = None


class StateLoadRequest(BaseModel):
    state: dict = Field(default_factory=dict)
    domain: str | None = None
    session: str | None = None


def _token() -> str:
    """Read the shared secret, every time.

    Re-read rather than cached so re-delivering it to a resumed sandbox takes
    effect without a restart -- the workspace runtime's token is consumed and
    unlinked on read, and copying that arrangement here would mean a resumed
    sandbox could never be re-authenticated.
    """
    try:
        return TOKEN_PATH.read_text().strip()
    except OSError:
        return ""


def _authenticate(provided: str) -> bool:
    expected = _token()
    if not expected:
        # No token delivered means nothing may talk to this yet. Fail closed:
        # an unauthenticated relay in a sandbox holding a signed-in browser is
        # the exact shape of the problem this design set out to remove.
        return False
    return hmac.compare_digest(provided.strip(), expected)


async def require_token(request: Request) -> None:
    """Refuse anything without the delivered token.

    The annotation is load-bearing: unannotated, FastAPI reads `request` as a
    *query parameter* rather than injecting the request, and every route behind
    this dependency answers 422 "field required" instead of ever checking a
    token. That is how it shipped the first time, and no unit test that called
    `_authenticate` directly could see it -- only running the relay in the image
    and asking for a route did.
    """
    if not _authenticate(request.headers.get("x-lemma-relay-token", "")):
        raise HTTPException(status_code=401, detail="relay token does not match")


def _session_name(session: str | None, domain: str | None) -> str:
    """Which browser session a request means.

    A caller-supplied name is checked before it is used, because it becomes a
    profile directory. Refused with a 422 rather than coerced: a name silently
    rewritten would point the browser somewhere the caller did not ask for and
    still report success. A name *derived* from a domain is safe by
    construction, but goes through the same check so there is one answer to
    "what may a session be called".
    """
    candidate = session or (session_for_domain(domain) if domain else DEFAULT_SESSION)
    if candidate != DEFAULT_SESSION and not is_safe_session(candidate):
        raise HTTPException(
            status_code=422, detail=f"{candidate!r} is not a usable session name"
        )
    return candidate


def create_app() -> FastAPI:
    app = FastAPI(title="Lemma browser relay", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> dict:
        """Whether Chrome is up, without starting it.

        A paused or idle workspace has no browser, and that is its resting
        state rather than a fault -- so this reports it as one and never
        conjures a browser to answer a health check.
        """
        try:
            await live_port()
        except BrowserNotRunning:
            return {"chrome": "stopped"}
        return {"chrome": "running"}

    @app.get("/targets", dependencies=[Depends(require_token)])
    async def targets() -> dict:
        try:
            port = await live_port()
            return {"targets": await page_targets(port=port)}
        except BrowserNotRunning:
            raise HTTPException(status_code=409, detail="the browser is not running")

    @app.post(
        "/browser:ensure",
        response_model=EnsureResponse,
        dependencies=[Depends(require_token)],
    )
    async def ensure(request: EnsureRequest) -> EnsureResponse:
        """Make sure there is a browser, on the right page, and say which.

        Starting and steering are one call because they are one question:
        somebody is about to be shown this browser, and both "is it up" and "is
        it on the site we told them about" have to be true before they arrive.
        """
        session = _session_name(request.session, request.domain)
        started = False
        try:
            await live_port(session)
        except BrowserNotRunning:
            started = True

        try:
            if request.origin:
                # `open` starts the browser if it is down, so this covers both.
                await open_url(request.origin, session=session)
            port = await ensure_port(session=session)
            found = await page_targets(port=port)
        except BrowserNotRunning as exc:
            raise HTTPException(status_code=409, detail=str(exc))

        if not found:
            raise HTTPException(status_code=409, detail="the browser has no page")
        target = _best_target(found, request.origin)
        return EnsureResponse(
            target_id=target["id"], url=target["url"], started=started
        )

    @app.post("/state:save", dependencies=[Depends(require_token)])
    async def state_save(request: StateSaveRequest) -> dict:
        session = _session_name(request.session, request.domain)
        try:
            return {"state": await save_session(session=session)}
        except StateOperationFailed as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/state:load", status_code=204, dependencies=[Depends(require_token)])
    async def state_load(request: StateLoadRequest) -> None:
        session = _session_name(request.session, request.domain)
        try:
            await load_session(request.state, session=session)
        except StateOperationFailed as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/state:clear", status_code=204, dependencies=[Depends(require_token)])
    async def state_clear(request: StateSaveRequest) -> None:
        await clear_session(session=_session_name(request.session, request.domain))

    @app.websocket("/session")
    async def session_socket(
        websocket: WebSocket,
        target: str = Query(default=""),
        session: str = Query(default=""),
        mode: str = Query(default=VIEW),
    ) -> None:
        """One viewer, watching or driving one page.

        Refused before `accept()` where it can be: a socket that is opened and
        then closed looks to a browser like a connection that dropped, and the
        person is told the wrong thing about why.
        """
        if not _authenticate(websocket.headers.get("x-lemma-relay-token", "")):
            await websocket.close(code=CLOSE_UNAUTHENTICATED)
            return
        if mode not in (VIEW, CONTROL):
            await websocket.close(code=CLOSE_UNAUTHENTICATED)
            return

        session_name = session or DEFAULT_SESSION
        if session_name != DEFAULT_SESSION and not is_safe_session(session_name):
            # Before `accept()`: a socket opened and then closed looks to a
            # browser like a connection that dropped.
            await websocket.close(code=CLOSE_UNAUTHENTICATED)
            return
        try:
            port = await live_port(session_name)
            target_id = target or _first_target_id(await page_targets(port=port))
        except BrowserNotRunning:
            await websocket.close(code=CLOSE_NO_BROWSER)
            return
        if not target_id:
            await websocket.close(code=CLOSE_NO_BROWSER)
            return

        await websocket.accept()
        await websocket.send_json({"t": "status", "state": "attached"})

        # Background: the keepalive outlives no request and belongs to the
        # browser rather than to whoever opened this socket.
        keepalive_task = create_background_task(_keepalive_loop(session_name))
        try:
            async with websockets.connect(
                page_socket_url(target_id, port=port),
                max_size=_MAX_CDP_FRAME_BYTES,
            ) as cdp_socket:
                cdp = CdpConnection(cdp_socket)
                viewer = ScreencastSession(cdp, mode=mode)
                await viewer.start()
                try:
                    await pump(
                        cdp_socket,
                        viewer,
                        send_json=websocket.send_json,
                        receive_text=_receiver(websocket),
                    )
                finally:
                    await viewer.stop()
        except OSError, websockets.exceptions.WebSocketException:
            with suppress(RuntimeError):
                await websocket.close(code=CLOSE_UPSTREAM_GONE)
        finally:
            keepalive_task.cancel()
            # Awaited, not just cancelled: a cancelled task is not finished
            # until it has been collected, and leaving it uncollected is how a
            # socket outlives the request that opened it.
            with suppress(asyncio.CancelledError):
                _ = await keepalive_task

    return app


def _receiver(websocket: WebSocket):
    async def receive_text() -> str | None:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return None
        return message.get("text")

    return receive_text


async def _keepalive_loop(session: str) -> None:
    while True:
        await asyncio.sleep(_KEEPALIVE_SECONDS)
        await keepalive(session=session)


def _first_target_id(targets: list[dict[str, str]]) -> str:
    return targets[0]["id"] if targets else ""


def _best_target(targets: list[dict[str, str]], origin: str | None) -> dict[str, str]:
    """The page for this origin if there is one, else whatever is frontmost.

    Matching on the origin rather than taking the first target is what makes
    "open the site, then attach" reliable when the browser already had other
    tabs open -- which it does, whenever the agent was working before it asked
    for help.
    """
    if origin:
        host = origin.split("://")[-1].split("/")[0].lower()
        for target in targets:
            if host and host in target.get("url", "").lower():
                return target
    return targets[0]
