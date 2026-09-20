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
import hmac
import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket
from pydantic import BaseModel, Field
import websockets

from sandbox_runtime.tasks import create_background_task

from .chrome import (
    answers_on,
    BrowserNotRunning,
    DEFAULT_SESSION,
    is_safe_session,
    keepalive,
    default_display_size,
    ensure_port,
    live_port,
    open_url,
    page_targets,
    RecordingInProgress,
    ensure_vnc_bridge,
    set_display_size,
)
from .stream_proxy import CONTROL, VIEW, pump_binary
from .cookies import forget_domains, list_cookie_domains
from .marks import forget_marks, mark_signed_in, signed_in_sites

#: Deliberately not under `/tmp/lemma-browser`, which `quiesce` deletes before a
#: pause: the token has to survive a resume, and the browser profile must not.
TOKEN_PATH = Path(os.environ.get("LEMMA_RELAY_TOKEN_FILE", "/tmp/lemma-relay/token"))

DEFAULT_PORT = int(os.environ.get("LEMMA_BROWSER_RELAY_PORT", "4850"))

#: Where websockify fronts x11vnc, on this same loopback -- both started by
#: `start-browser.sh` alongside Xvfb. Read at request time like the rest of
#: this file's config, not cached, in case a resumed sandbox is handed a
#: different value than the one it was created with.
VNC_WS_PORT = int(os.environ.get("LEMMA_BROWSER_VNC_WS_PORT", "5901"))

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

#: A frame from `websockify`. Bounded so a page cannot make one viewer's
#: socket into this process's memory problem.
_MAX_FRAME_BYTES = 8 * 1024 * 1024

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


class DisplayResizeRequest(BaseModel):
    """The size a viewer wants the sandbox display to be.

    Bounded here rather than trusted: these numbers come from a browser
    window, and a display is a framebuffer somebody else's memory pays for.
    The script clamps again against the framebuffer Xvfb actually allocated,
    which is the limit that cannot be argued with.
    """

    width: int = Field(ge=320, le=4096)
    height: int = Field(ge=240, le=4096)


class DisplayResizeResponse(BaseModel):
    #: What the display ended up as, which is not always what was asked for --
    #: see the clamping in `set-display-size`.
    size: str


class ForgetRequest(BaseModel):
    #: Exact cookie hosts, chosen by the backend. The relay does not know
    #: which of them are "one site" and must not guess -- see `cookies.py`.
    domains: list[str] = Field(default_factory=list)
    #: The registrable domain those hosts belong to, so the "they signed in
    #: here" mark goes with them. Grouping is the backend's question, so the
    #: answer arrives rather than being worked out here.
    sites: list[str] = Field(default_factory=list)


class SignedInRequest(BaseModel):
    #: One registrable domain, already grouped by the backend.
    site: str


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
    still report success.

    `domain` no longer derives one. A sign-in used to open a browser named for
    its site so that a capture taken from it could only contain that site;
    nothing is captured now, and everything shares the one durable profile, so
    a domain says which page to open and nothing about which browser.
    """
    del domain
    candidate = session or DEFAULT_SESSION
    if candidate != DEFAULT_SESSION and not is_safe_session(candidate):
        raise HTTPException(
            status_code=422, detail=f"{candidate!r} is not a usable session name"
        )
    return candidate


#: How many VNC sockets this relay is serving right now.
#:
#: Counted here rather than in the API, which was counting in a
#: process-local dict: two people watching one sandbox can arrive through
#: different API workers, and the first to leave then reset the display
#: under the second. One sandbox has exactly one relay, so this is the only
#: place the question has a single answer.
_viewers = 0


def create_app() -> FastAPI:
    app = FastAPI(title="Lemma browser relay", docs_url=None, redoc_url=None)

    async def _vnc_is_listening() -> bool:
        """Whether websockify is accepting connections on its port.

        The picture is `x11vnc` in front of Xvfb with `websockify` in front
        of that, and none of it is this process -- so "the relay answered"
        has never meant "a viewer will get a picture", and neither does "the
        bridge script exited 0". Probing the socket is the only thing that
        does.

        `chrome.answers_on` rather than a second copy of the same six
        lines: this was one, they drifted on the timeout, and it is the
        seam the tests already reach for when they need a port to be up or
        down.
        """
        return await answers_on(VNC_WS_PORT)

    @app.get("/health")
    async def health() -> dict:
        """Whether Chrome is up, and whether a viewer could see it.

        A paused or idle workspace has no browser, and that is its resting
        state rather than a fault -- so this reports it as one and never
        conjures a browser to answer a health check.

        `vnc` is separate from `chrome` because they fail separately and the
        remedies differ. A viewer that could not get a picture used to close
        with 4409, "the browser is not running", which was the same answer
        for a browser that was down, a display that never came up, and a
        websockify that had died -- three faults, one sentence, and no way
        to tell them apart from outside the sandbox.
        """
        vnc = "listening" if await _vnc_is_listening() else "down"
        _ = _viewers
        try:
            await live_port()
        except BrowserNotRunning:
            return {"chrome": "stopped", "vnc": vnc, "viewers": _viewers}
        return {"chrome": "running", "vnc": vnc, "viewers": _viewers}

    @app.get("/targets", dependencies=[Depends(require_token)])
    async def targets(
        session: str = Query(default=""),
        domain: str = Query(default=""),
    ) -> dict:
        """Open pages in one session, named the same way `/browser:ensure` is.

        `session`/`domain` resolve exactly as they do there -- a caller asking
        after a sign-in's own session passes `domain`, not a name it would
        have to reconstruct. Defaults to the default session, which is what
        every caller before this one wanted.
        """
        session_name = _session_name(session or None, domain or None)
        try:
            port = await live_port(session_name)
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

    @app.post(
        "/display:resize",
        response_model=DisplayResizeResponse,
        dependencies=[Depends(require_token)],
    )
    async def display_resize(request: DisplayResizeRequest) -> DisplayResizeResponse:
        """Make the display the shape of the pane it is being watched in.

        The alternative, and what this replaces, was one fixed display scaled
        to fit whatever box it landed in: a 3:2 picture letterboxed into a
        narrow sidebar, small and surrounded by dead space. Resizing the
        display itself means the pixels sent are the pixels shown, and a
        narrow pane gets a narrow *viewport* -- so a site serves its mobile
        layout to somebody signing in on a phone rather than a shrunken
        desktop one.

        noVNC's own `resizeSession` cannot do this for us: it refuses while
        the client is view-only, and watching is the default here.

        **Clamped to the starting size, not to the framebuffer ceiling.** A
        maximised pane on a large monitor would otherwise leave a 1 vCPU /
        2 GB sandbox running at 1920x1200 -- 1.67x the pixels for x11vnc to
        encode and for `agent-browser record` to grab, which was measured to
        lose a recording part-way through on a loaded runner. An agent that
        genuinely wants the ceiling can still ask for it with
        `set-display-size`, deliberately, for as long as it needs; a person
        opening a panel should not be able to do it by accident.
        """
        cap_width, cap_height = default_display_size()
        try:
            size = await set_display_size(
                min(request.width, cap_width), min(request.height, cap_height)
            )
        except RecordingInProgress as exc:
            # 409 rather than a silent no-op, and a reason the pane can show.
            # A person opening the panel while an agent is recording used to
            # move the framebuffer under the recorder; the picture is
            # letterboxed until the take ends instead, which is the
            # recoverable half of the trade.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if size is None:
            raise HTTPException(
                status_code=409, detail="the display could not be resized"
            )
        return DisplayResizeResponse(size=size)

    @app.post(
        "/display:reset",
        response_model=DisplayResizeResponse,
        dependencies=[Depends(require_token)],
    )
    async def display_reset() -> DisplayResizeResponse:
        """Put the display back to its starting size.

        Called when the last person watching disconnects. Without it the
        sandbox kept whichever shape the last pane happened to be for the
        rest of its life -- so an agent taking a screenshot or a recording
        afterwards inherited the dimensions of a sidebar it could not see and
        had no way to know about. A predictable resting size is something it
        can plan against.
        """
        width, height = default_display_size()
        try:
            size = await set_display_size(width, height)
        except RecordingInProgress as exc:
            # The last viewer leaving must not resize either. This is the
            # more dangerous of the two paths, because nobody is watching
            # when it fires: the agent is alone with its recording and the
            # reset would land in the middle of it. The display keeps the
            # viewer's shape until the take ends, and the next reset -- or
            # the next viewer -- puts it back.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if size is None:
            raise HTTPException(status_code=409, detail="the display did not reset")
        return DisplayResizeResponse(size=size)

    @app.get("/profile:cookies", dependencies=[Depends(require_token)])
    async def profile_cookies(start: bool = False) -> dict:
        """Which hosts the browser holds cookies for. No values, ever.

        The cookies are read over CDP, so this needs Chrome running -- and
        Chrome not running does *not* mean there are no logins: the profile
        is on disk either way, and the daemon retires the browser after five
        idle minutes. So "not running" is "cannot say", not "nothing", and
        `start` is the caller saying it is willing to pay for an answer.

        `signed_in` rides along in both branches because it costs a file
        read rather than a browser: it is the set of sites somebody said
        they signed in to, which is the only thing here that distinguishes a
        login from a tracking cookie. See `marks.py` for why nothing tries
        to work that out from the cookies themselves.
        """
        marked = signed_in_sites()
        try:
            port = await ensure_port() if start else await live_port()
        except BrowserNotRunning:
            return {"running": False, "cookies": [], "signed_in": marked}
        return {
            "running": True,
            "cookies": await list_cookie_domains(port=port),
            "signed_in": marked,
        }

    @app.post("/profile:signed-in", dependencies=[Depends(require_token)])
    async def profile_signed_in(request: SignedInRequest) -> dict:
        """Record that somebody signed in to this site.

        No browser needed: this is a fact a person stated, not one read off
        the profile, and it must survive being recorded while Chrome is
        between idle retirements.
        """
        return {"signed_in": mark_signed_in(request.site)}

    @app.post("/profile:forget", dependencies=[Depends(require_token)])
    async def profile_forget(request: ForgetRequest) -> dict:
        try:
            port = await live_port()
        except BrowserNotRunning:
            raise HTTPException(status_code=409, detail="the browser is not running")
        outcome = await forget_domains(request.domains, port=port)
        if outcome.refused:
            # The mark stays, and so does the failure. `clearDataForOrigin`
            # is the only thing on this path that deletes anything, so a
            # refusal means the session is still live -- and a person told
            # "signed out" over a live session stops looking, which is the
            # worst of the outcomes available here. Raising also reaches
            # them: `forget_cookies` turns a non-200 into
            # `BrowserRelayUnavailable` rather than a silent zero.
            raise HTTPException(
                status_code=502,
                detail=(
                    f"the browser refused to clear {outcome.refused} of "
                    f"{outcome.origins} origins; still signed in"
                ),
            )
        # The mark goes even when no cookie did. A site whose session had
        # already lapsed would otherwise keep reading "signed in" for as
        # long as the profile lived, with nothing left to sign out of.
        return {
            "dropped": outcome.dropped,
            "signed_in": forget_marks(request.sites),
        }

    @app.websocket("/vnc")
    async def vnc_socket(
        websocket: WebSocket,
        mode: str = Query(default=VIEW),
        session: str = Query(default=""),
    ) -> None:
        """One viewer of this sandbox's whole display, over VNC.

        Names no target: all of a sandbox's `agent-browser` sessions share
        one Xvfb display, so VNC shows whatever is on `:99`, not a
        CDP-selected tab. In practice only one session's browser is normally
        alive at a time -- the idle timeout retires the rest -- so this is
        the accepted trade rather than a bug; per-session display isolation
        is future work.

        `session` is not a selector either -- it cannot be, for the same
        reason -- but it is still read, because the liveness check and the
        keepalive are both about one session's browser rather than about the
        screen. The caller resolves it beforehand: `/browser:ensure`'s reply
        says which session a steer actually landed in.
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
            # `session_name`, not the bare default: a sign-in's Chrome runs in
            # its own named session (its own profile, its own port), and
            # `live_port()` with no argument checks only the default one's.
            # Checking the wrong session here reported "no browser running"
            # about a browser that was on screen at the time -- the picture
            # is shared, but whether *a* Chrome process is up is still asked
            # per session, and the login session's was never the one asked.
            await live_port(session_name)
        except BrowserNotRunning as exc:
            await _refuse(websocket, CLOSE_NO_BROWSER, f"no browser running: {exc}")
            return

        # The viewing chain is not started with the display -- x11vnc and
        # websockify serve a person watching, and most sessions have nobody
        # watching at all. So it is started here, by the route that is about
        # to need it, before the socket is accepted: `_refuse` before accept
        # is a clean refusal the pane can read, and a failure after accept is
        # a dropped picture with no reason attached.
        started = await ensure_vnc_bridge()
        # Probed rather than trusted, and probed even when the script said
        # yes. `ensure_vnc_bridge` reports what a shell script exited with;
        # this asks the question the viewer actually cares about. They came
        # apart in CI: the bridge exited 0, the relay accepted, and the
        # socket then died mid-RFB with no close frame and no reason -- the
        # one failure shape a person cannot act on, because `accept()` has
        # already happened and there is nowhere left to put a reason.
        #
        # One loopback connect, on a path that is about to proxy every
        # frame of a screen through that same port.
        if not await _vnc_is_listening():
            await _refuse(
                websocket,
                CLOSE_UPSTREAM_GONE,
                (
                    f"nothing is serving VNC on {VNC_WS_PORT}"
                    + ("" if started else "; the bridge did not come up")
                ),
            )
            return

        await websocket.accept()
        global _viewers
        _viewers += 1
        # Watching is not a command, so without this the agent's idle timeout
        # retires the browser out from under somebody reading the page.
        #
        # `session_name`, not the default -- the same distinction the liveness
        # check above already makes. This used to keep the *default* session
        # warm while a sign-in ran in `login-<host>` and a watch named its
        # conversation's session -- so the browser actually on screen went
        # idle and retired mid-page, taking a sign-in with it, while a browser
        # nobody was watching was held open in a sandbox whose memory guard
        # kills on ~220 MB free. There is one session now, so the name this
        # keeps warm and the one being watched cannot disagree.
        keepalive_task = create_background_task(_keepalive_loop(session_name))
        # Nothing is claimed here. There was a lease -- a file the agent's own
        # commands read before acting, so a person driving could not be typed
        # over -- and it was removed because it never covered the case it was
        # written for and only ever cost the case it did reach. A sign-in runs
        # in `login-<host>`, a session the agent never touches, so the lease
        # was a no-op at the one moment somebody was typing a password; an
        # ordinary watch attaches to the agent's *own* session, so the only
        # thing it ever stopped was the agent using its own browser while
        # somebody looked at it. Two parties acting at once costs a retry,
        # which is cheaper than stalling the run.
        try:
            async with websockets.connect(
                f"ws://127.0.0.1:{VNC_WS_PORT}/",
                max_size=_MAX_FRAME_BYTES,
            ) as upstream:
                await pump_binary(
                    upstream,
                    mode=mode,
                    send_bytes=websocket.send_bytes,
                    receive_bytes=_receiver_bytes(websocket),
                )
        except OSError, websockets.exceptions.WebSocketException:
            with suppress(RuntimeError):
                await websocket.close(code=CLOSE_UPSTREAM_GONE)
        finally:
            _viewers = max(0, _viewers - 1)
            keepalive_task.cancel()
            # Awaited, not just cancelled: a cancelled task is not finished
            # until it has been collected, and leaving it uncollected is how a
            # socket outlives the request that opened it.
            with suppress(asyncio.CancelledError):
                _ = await keepalive_task

    return app


def _receiver_bytes(websocket: WebSocket):
    async def receive_bytes() -> bytes | None:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return None
        data = message.get("bytes")
        if data is not None:
            return data
        text = message.get("text")
        return text.encode() if text is not None else b""

    return receive_bytes


async def _keepalive_loop(session: str) -> None:
    while True:
        await asyncio.sleep(_KEEPALIVE_SECONDS)
        await keepalive(session=session)


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
