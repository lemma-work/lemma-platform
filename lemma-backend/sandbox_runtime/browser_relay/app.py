"""The browser relay: one door into this sandbox's browser, for the backend.

Why this is a separate process rather than a route on the workspace runtime:
**E2B sandboxes do not run the workspace runtime.** They serve no HTTP inside
the sandbox at all -- exec and files go through the provider's own SDK -- so a
browser channel that lived in the runtime worked on Docker and existed nowhere
else, which is precisely what happened the first time this was built. A small
process baked into the image runs wherever the image runs: Docker, E2B, the
desktop guest, and anything later that can start a container.

Why not publish the browser's own ports and let a viewer reach them directly: on
E2B every port is a public name, so a forwarded CDP port would put raw Chrome
debugging protocol -- which reads every cookie and evaluates arbitrary script --
behind nothing but a traffic token, and `agent-browser`'s stream server has no
authentication of its own at all. Everything it serves is reached *through* this
process instead, which is the thing holding the delivered token.

The token is read from a file the backend places through the provider's own
secret-delivery path, and re-read on every request so a resumed sandbox can be
handed a fresh one without restarting anything.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
import hashlib
import hmac
import secrets
import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket
from pydantic import BaseModel, Field
import websockets

from sandbox_runtime.tasks import create_background_task

from .chrome import (
    BrowserNotRunning,
    DEFAULT_SESSION,
    is_safe_session,
    ensure_port,
    keepalive,
    live_port,
    open_url,
    page_targets,
    stream_port,
    stream_socket_url,
)
from .stream_proxy import CONTROL, VIEW, pump
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

#: The session a viewer watches when no particular one is named.
#:
#: Taken from `chrome.py` rather than re-read from `AGENT_BROWSER_SESSION`: the
#: agent's browser script exports that name into its conversation's shell, the
#: shell is persistent, and this process is started by an exec into the same
#: sandbox -- so reading it here meant one conversation could rename "the
#: default" for everybody. See the note on `chrome.DEFAULT_SESSION`.

#: How often to touch the browser while somebody is watching. Comfortably inside
#: agent-browser's two-minute idle timeout, which counts *commands* -- and
#: watching is not one, so without this the browser retires under a person who
#: is reading the page.
_KEEPALIVE_SECONDS = 45.0

#: A frame from the stream server. Bounded so a page cannot make one viewer's
#: socket into this process's memory problem.
_MAX_STREAM_FRAME_BYTES = 8 * 1024 * 1024

#: The ceiling this relay asks the stream for. A person watching a browser is
#: reading a page, not watching a film; past this the bytes buy nothing and a
#: phone pays for them. A client may ask for fewer with a `config` message.
_MAX_FPS = 15

CLOSE_UNAUTHENTICATED = 4401
CLOSE_NO_BROWSER = 4409
CLOSE_UPSTREAM_GONE = 1011

_log = logging.getLogger(__name__)


async def _refuse(websocket: WebSocket, code: int, reason: str) -> None:
    """Close so that the caller is told which refusal this was.

    A close sent *before* `accept()` is not a close: ASGI turns it into a
    rejected handshake, and a rejected handshake carries an HTTP status and no
    close frame at all. Every refusal below then reached the API as one
    indistinguishable `InvalidStatus`, which it reported to the pane as 1011 --
    "the connection dropped" -- and the pane retried, for ever, because 1011 is
    the code it is right to retry.

    So the ordinary resting state of an idle workspace ("the browser is not
    running", which is not a failure) was shown to the person as a fault, on a
    loop. The same mistake, for the same reason, as the one written out at
    length in `browser_view_controller._refuse`; this is the sandbox half of it.

    Accepting a socket in order to close it is backwards, and is correct anyway
    because nothing is sent in between: the caller gets an open, a close frame
    carrying the reason, and no bytes.
    """
    # Logged on the way out, every time. A close code is four digits reaching
    # somebody through two processes and a fabric proxy; without a line here
    # saying which branch produced it, diagnosing one means adding this line.
    _log.warning("refusing a viewer: %s (%d)", reason, code)
    # Suppressed rather than checked: the caller may have gone between the
    # handshake and here, and a refusal that cannot be delivered must not become
    # a traceback of its own.
    with suppress(RuntimeError):
        await websocket.accept()
    with suppress(RuntimeError):
        await websocket.close(code=code)


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
    #: What the page calls itself. Carried so a caller can tell a site that
    #: accepted a restored session from one that bounced it to a login form,
    #: without a second round trip to read the page.
    title: str = ""
    started: bool
    #: The session this target actually lives in.
    #:
    #: Returned rather than left for the caller to work out again, because a
    #: target id is only meaningful against the Chrome that minted it -- every
    #: session is a separate browser with its own profile and its own port. The
    #: backend used to re-derive the name from the origin on its own and reach a
    #: different answer from this one, so it attached a viewer to `login-<host>`
    #: carrying a target id from `workspace`. Saying which session was used is
    #: what makes the two sides unable to disagree.
    session: str


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
            target_id=target["id"],
            url=target["url"],
            title=target.get("title", ""),
            started=started,
            session=session,
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

        Every refusal goes through `_refuse`, which accepts the socket before
        closing it -- that is the only way the reason survives as a close code
        rather than as an HTTP status nobody downstream can read.
        """
        if not _authenticate(websocket.headers.get("x-lemma-relay-token", "")):
            await _refuse(
                websocket, CLOSE_UNAUTHENTICATED, "no token, or the wrong one"
            )
            return
        if mode not in (VIEW, CONTROL):
            await _refuse(websocket, CLOSE_UNAUTHENTICATED, f"{mode!r} is not a mode")
            return

        session_name = session or DEFAULT_SESSION
        if session_name != DEFAULT_SESSION and not is_safe_session(session_name):
            await _refuse(
                websocket, CLOSE_UNAUTHENTICATED, f"{session_name!r} is not a session"
            )
            return
        try:
            port = await live_port(session_name)
            open_targets = await page_targets(port=port)
        except BrowserNotRunning as exc:
            await _refuse(
                websocket, CLOSE_NO_BROWSER, f"no browser in {session_name!r}: {exc}"
            )
            return

        # A target id only means anything against the Chrome that minted it.
        # Sessions are separate browsers on separate ports, so a caller that
        # worked out the session one way and the target another produces an id
        # this browser has never heard of. The stream itself would not notice:
        # it is session-scoped and follows that session's active tab, so a
        # mismatched target would stream somebody a *different browser* and look
        # entirely healthy doing it. That is the bug this feature shipped with,
        # and this is the check that makes it impossible: the two halves of the
        # answer have to agree here, or nobody is attached at all.
        #
        # What it is not is a selector. The stream shows the active tab, and
        # there is no inbound message that changes which one that is.
        known = {found["id"] for found in open_targets}
        if target and target not in known:
            await _refuse(
                websocket,
                CLOSE_NO_BROWSER,
                f"target {target} is not open in {session_name!r}",
            )
            return

        if not (target or _first_target_id(open_targets)):
            await _refuse(
                websocket, CLOSE_NO_BROWSER, f"no page open in {session_name!r}"
            )
            return

        try:
            stream = await stream_port(session=session_name)
        except BrowserNotRunning as exc:
            await _refuse(
                websocket, CLOSE_NO_BROWSER, f"no stream in {session_name!r}: {exc}"
            )
            return

        await websocket.accept()
        await websocket.send_json({"type": "status", "state": "attached"})

        # Background: the keepalive outlives no request and belongs to the
        # browser rather than to whoever opened this socket.
        keepalive_task = create_background_task(_keepalive_loop(session_name))
        driving = _take_the_wheel(session_name) if mode == CONTROL else None
        try:
            async with websockets.connect(
                stream_socket_url(stream, max_fps=_MAX_FPS),
                max_size=_MAX_STREAM_FRAME_BYTES,
            ) as stream_socket:
                await pump(
                    stream_socket,
                    mode=mode,
                    send_text=websocket.send_text,
                    receive_text=_receiver(websocket),
                )
        except OSError, websockets.exceptions.WebSocketException:
            with suppress(RuntimeError):
                await websocket.close(code=CLOSE_UPSTREAM_GONE)
        finally:
            _release_the_wheel(driving)
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

    The comparison is on the parsed host, not on the URL as a string. A
    substring test matched `https://attacker.test/#bank.com` for host
    `bank.com`, which is the wrong tab to hand somebody who was told they are
    signing in to their bank.
    """
    if origin:
        host = _host_of(origin)
        for target in targets:
            if host and _host_of(target.get("url", "")) == host:
                return target
    return targets[0]


def _host_of(url: str) -> str:
    """The host part of a URL, lowercased, without port or credentials."""
    authority = url.split("://")[-1].split("/")[0].lower()
    # `user:pass@host:port` -- the host is what is left after the last `@` and
    # before the first `:`.
    return authority.rpartition("@")[2].split(":")[0]


#: Where a control session records that somebody is driving. Under the relay's
#: own directory rather than the browser profile's, because `quiesce` deletes
#: the profile and a lease that vanished with it would read as "nobody is
#: driving" to the next command.
_WHEEL_DIR = Path("/tmp/lemma-relay/wheel")


def wheel_path(session: str) -> Path:
    """The lease file for one session's browser."""
    digest = hashlib.sha256(session.encode()).hexdigest()[:32]
    return _WHEEL_DIR / digest


@dataclass(frozen=True, slots=True)
class _Wheel:
    """One viewer's claim on a session, and the proof that it is theirs."""

    path: Path
    token: str


def _take_the_wheel(session: str) -> "_Wheel | None":
    """Mark this session as being driven by a person.

    A file rather than state in this process, because the other party is not in
    this process: the agent's commands run in a shell, and what has to see the
    lease is the script they run. Both are in this sandbox, so the filesystem is
    the one thing they share.

    Named from a digest for the same reason the profile directory is -- a
    session name is a caller's string and must not become a path.
    """
    path = wheel_path(session)
    # A token of this holder's own, written into the file. Without one the
    # lease was just "a file exists", so two people driving the same session
    # meant whichever of them closed *first* released it -- and the one still
    # holding the wheel silently lost it, with the agent free to type into the
    # page they were using.
    token = secrets.token_hex(16)
    try:
        _WHEEL_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(token)
        return _Wheel(path=path, token=token)
    except OSError:
        # Not being able to take the lease must not stop somebody watching. The
        # cost is that an agent command may land at the same time, which is what
        # happened before this existed at all.
        _log.warning("could not record the control lease for session %s", session)
        return None


def _release_the_wheel(held: "_Wheel | None") -> None:
    """Give up the lease, but only if it is still ours.

    A later viewer's token in the file means they took it after us, and it is
    theirs to release. Read-then-unlink is not atomic and does not need to be:
    the loser of that race releases a lease that was about to be re-taken, and
    the next command re-reads the file rather than trusting a decision made
    earlier.
    """
    if held is None:
        return
    with suppress(OSError):
        if held.path.read_text().strip() != held.token:
            return
    with suppress(OSError):
        held.path.unlink(missing_ok=True)
