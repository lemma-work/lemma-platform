"""The relay, driven against a fake Chrome and a fake browser CLI.

The bugs this is guarding against are all ones that produced *no error*: a
blank panel, a stream that stopped after one frame, a start that hung forever
while the browser it was waiting for was up and healthy. So the assertions here
are mostly about what happened rather than about what came back.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sandbox_runtime.browser_relay import chrome
from sandbox_runtime.paths import HOME_ROOT
from sandbox_runtime.browser_relay.app import TOKEN_PATH, create_app
from sandbox_runtime.browser_relay.stream_proxy import CONTROL, VIEW

# No module-level `pytest.mark.asyncio`: pytest-asyncio runs in auto mode here,
# so async tests are collected without it, and marking the synchronous ones in
# this file warns.


# ---------------------------------------------------------------------------
# Starting Chrome: the three traps
# ---------------------------------------------------------------------------


class _FakeStdout:
    """A pipe that yields lines and then never ends.

    Which is the real behaviour: `start-browser` leaves Xvfb and the daemon
    holding the CLI's stdout, so the pipe stays open after the CLI itself has
    exited. Anything that waits for EOF waits forever.
    """

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    def feed_eof(self) -> None:
        pass


class _FakeProcess:
    def __init__(self, lines: list[bytes], *, exits: bool = True) -> None:
        self.stdout = _FakeStdout(lines)
        self.returncode = 0 if exits else None
        self.killed = False
        self._exits = exits

    async def wait(self) -> int:
        if not self._exits:
            await asyncio.sleep(3600)
        return 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


async def test_the_port_is_read_line_by_line_not_to_eof(monkeypatch) -> None:
    """The bug that made a takeover hang while the browser was fine."""
    process = _FakeProcess(
        [b"starting browser\n", b"cdp: ws://127.0.0.1:45123/devtools/browser/x\n"]
    )

    async def fake_exec(*_argv, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    port = await asyncio.wait_for(chrome.ensure_port(), timeout=5)
    assert port == 45123


async def test_a_cli_that_never_exits_is_killed_rather_than_waited_on(
    monkeypatch,
) -> None:
    process = _FakeProcess(
        [b"cdp: ws://127.0.0.1:45124/devtools/browser/x\n"], exits=False
    )

    async def fake_exec(*_argv, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(chrome, "_REAP_TIMEOUT_SECONDS", 0.05)

    assert await asyncio.wait_for(chrome.ensure_port(), timeout=5) == 45124
    assert process.killed, "a wedged CLI must not outlive the answer it gave"


async def test_a_recorded_port_nothing_answers_on_is_not_believed(
    monkeypatch, tmp_path: Path
) -> None:
    """Chrome leaves DevToolsActivePort behind when it exits, and it exits often."""
    port_file = tmp_path / "DevToolsActivePort"
    port_file.write_text("45999\n/devtools/browser/abc\n")
    monkeypatch.setattr(chrome, "_ACTIVE_PORT_FILE", port_file)

    async def nothing_listening(_port: int) -> bool:
        return False

    monkeypatch.setattr(chrome, "_answers_on", nothing_listening)

    assert chrome.recorded_port() == 45999
    with pytest.raises(chrome.BrowserNotRunning):
        await chrome.live_port()


async def test_a_recorded_port_that_answers_is_used(
    monkeypatch, tmp_path: Path
) -> None:
    port_file = tmp_path / "DevToolsActivePort"
    port_file.write_text("45998\n")
    monkeypatch.setattr(chrome, "_ACTIVE_PORT_FILE", port_file)

    async def listening(_port: int) -> bool:
        return True

    monkeypatch.setattr(chrome, "_answers_on", listening)
    assert await chrome.live_port() == 45998


async def test_the_absolute_wrapper_is_preferred_over_the_bare_name(
    monkeypatch,
) -> None:
    """The runtime's PATH finds the binary that cannot bootstrap a cold container."""
    monkeypatch.setattr(Path, "exists", lambda self: True)
    argv = chrome.agent_browser_argv("get", "cdp-url", session="workspace")
    assert argv[0] == "/usr/local/bin/agent-browser"
    assert argv[1:3] == ["--session", "workspace"]


# ---------------------------------------------------------------------------
# The token
# ---------------------------------------------------------------------------


async def test_no_delivered_token_refuses_everything(monkeypatch, tmp_path) -> None:
    """Fail closed. An open relay in a sandbox holding a signed-in browser is
    the shape of problem this whole design removes."""
    from sandbox_runtime.browser_relay import app as relay_app

    monkeypatch.setattr(relay_app, "TOKEN_PATH", tmp_path / "absent")
    assert relay_app._authenticate("") is False
    assert relay_app._authenticate("anything") is False


async def test_the_token_is_reread_so_a_resume_can_refresh_it(
    monkeypatch, tmp_path
) -> None:
    """The workspace runtime consumes and unlinks its token, which is why a
    resumed sandbox has to be handed it again. Caching here would mean a
    resumed sandbox could never be re-authenticated without a restart."""
    from sandbox_runtime.browser_relay import app as relay_app

    token_file = tmp_path / "token"
    token_file.write_text("first")
    monkeypatch.setattr(relay_app, "TOKEN_PATH", token_file)
    assert relay_app._authenticate("first") is True

    token_file.write_text("second")
    assert relay_app._authenticate("first") is False
    assert relay_app._authenticate("second") is True


def test_the_token_file_is_not_where_quiesce_looks() -> None:
    """`quiesce` deletes /tmp/lemma-browser before a pause. The token has to
    survive that; the browser profile deliberately does not."""
    from sandbox_runtime.workspace.quiescer import WorkspaceQuiescer

    doomed = [str(p) for p in WorkspaceQuiescer._ephemeral_directories]
    assert not any(str(TOKEN_PATH).startswith(d + "/") for d in doomed), (
        f"{TOKEN_PATH} is inside a directory quiesce removes"
    )


# ---------------------------------------------------------------------------
# The profile, and what leaves the sandbox
# ---------------------------------------------------------------------------


def test_the_profile_is_on_the_durable_disk() -> None:
    """A login that does not survive a suspend is a login the person is asked
    for again next conversation, which is the whole complaint this answers."""
    assert chrome._DEFAULT_PROFILE == f"{HOME_ROOT}/.lemma/browser/profile"
    assert not chrome._DEFAULT_PROFILE.startswith("/tmp/")


def test_a_named_session_stays_scratch() -> None:
    """Naming a session is how an agent asks for a *second* browser -- two
    accounts side by side -- and that is a throwaway by construction. Letting
    those accumulate in the home would grow a profile per name anyone ever
    passed."""
    profile = chrome.profile_for_session("compare-b")
    assert profile is not None and profile.startswith("/tmp/")
    assert chrome.profile_for_session(chrome.DEFAULT_SESSION) is None


def test_a_hostile_session_name_cannot_escape_into_a_path() -> None:
    for hostile in ("a/../../etc/passwd", "a;rm -rf /", ".."):
        assert not chrome.is_safe_session(hostile)
        with pytest.raises(chrome.UnsafeSessionName):
            chrome.profile_for_session(hostile)


def test_no_cookie_value_can_leave_the_sandbox() -> None:
    """The relay reports hosts and expiries, never values.

    The design this replaced had to carry values out by construction -- the
    backend encrypted them and put them back later. This one does not, so the
    guarantee is the shape of the function rather than a rule about who may
    call it.
    """
    import inspect

    from sandbox_runtime.browser_relay import cookies

    source = inspect.getsource(cookies.list_cookie_domains)
    assert '"value"' not in source and "'value'" not in source


def test_the_relay_serves_only_what_it_means_to() -> None:
    """A route added here is a door into a signed-in browser, so the set is
    asserted rather than assumed."""
    app = create_app()
    served = {getattr(r, "path", "") for r in app.routes}
    assert {
        "/health",
        "/targets",
        "/browser:ensure",
        "/display:resize",
        "/display:reset",
        "/profile:cookies",
        "/profile:forget",
        "/profile:signed-in",
        "/vnc",
    } <= served
    # Asserted as an equality, not a subset: `/state:clear` was once here and
    # nothing ever called it, all the way down through the client and the
    # service. A route into a signed-in browser that no product path uses is
    # surface for free. `/state:save` and `/state:load` are gone for a larger
    # reason -- nothing lifts a session out of the sandbox any more.
    assert {p for p in served if p.startswith("/profile")} == {
        "/profile:cookies",
        "/profile:forget",
        # Names only, never a cookie: the set of sites somebody said they
        # signed in to, which is the one thing the cookie store cannot say.
        "/profile:signed-in",
    }
    # Equality here too, for the reason above. Both were absent from this
    # file while it claimed to assert the served surface, so a display route
    # could have come or gone without anything noticing.
    assert {p for p in served if p.startswith("/display")} == {
        "/display:resize",
        "/display:reset",
    }
    assert not {p for p in served if p.startswith("/state")}
    assert not {p for p in served if p.startswith("/cdp")}
    # `/session` proxied `agent-browser`'s own JSON/JPEG stream server. VNC
    # replaced it outright rather than living beside it, so a route this
    # relay no longer needs is exactly the surface the docstring above warns
    # against leaving behind.
    assert "/session" not in served


# ---------------------------------------------------------------------------
# The routes, driven through FastAPI rather than by calling helpers
# ---------------------------------------------------------------------------


def _client(monkeypatch, tmp_path, token: str = "token-abc"):
    """A test client over the real app, with a token file behind it.

    Every assertion below goes through FastAPI's own dependency resolution.
    That matters: the first version of the token dependency took an unannotated
    `request`, so FastAPI read it as a query parameter and every guarded route
    answered 422 without ever checking a token. Tests that called the
    authentication helper directly all passed.
    """
    from fastapi.testclient import TestClient

    from sandbox_runtime.browser_relay import app as relay_app

    token_file = tmp_path / "token"
    token_file.write_text(token)
    monkeypatch.setattr(relay_app, "TOKEN_PATH", token_file)
    return TestClient(relay_app.create_app())


def test_health_needs_no_token_and_says_when_chrome_is_down(
    monkeypatch, tmp_path
) -> None:
    """A paused workspace has no browser, and that is not a fault."""
    client = _client(monkeypatch, tmp_path)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"chrome": "stopped"}


def test_a_guarded_route_without_a_token_is_refused_as_unauthorised(
    monkeypatch, tmp_path
) -> None:
    client = _client(monkeypatch, tmp_path)
    response = client.get("/targets")
    assert response.status_code == 401, (
        "422 here means the dependency is reading a query parameter instead of "
        "the request, and the token is never checked at all"
    )


def test_a_guarded_route_with_the_wrong_token_is_refused(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    response = client.get("/targets", headers={"X-Lemma-Relay-Token": "nope"})
    assert response.status_code == 401


def test_the_right_token_reaches_the_route(monkeypatch, tmp_path) -> None:
    """409 is the honest answer: authenticated, but there is no browser up."""
    client = _client(monkeypatch, tmp_path)
    response = client.get("/targets", headers={"X-Lemma-Relay-Token": "token-abc"})
    assert response.status_code == 409


def test_every_profile_route_is_behind_the_token(monkeypatch, tmp_path) -> None:
    """These read and change a signed-in browser; none may be open."""
    client = _client(monkeypatch, tmp_path)
    for path, body in (
        ("/browser:ensure", {}),
        ("/profile:forget", {"domains": ["example.com"]}),
    ):
        assert client.post(path, json=body).status_code == 401, path
    assert client.get("/profile:cookies").status_code == 401


# ---------------------------------------------------------------------------
# A session name is a path segment
# ---------------------------------------------------------------------------


def test_a_session_name_cannot_escape_the_profile_directory() -> None:
    """CodeQL found this: the name becomes a directory, so it is checked where
    the path is built rather than only in the helper that derives one."""
    for attempt in ("../../etc", "..", ".", "a/b", "a\\b", "x\x00y", "/etc/passwd"):
        assert chrome.is_safe_session(attempt) is False, attempt
        with pytest.raises(chrome.UnsafeSessionName):
            chrome.profile_for_session(attempt)


def test_the_names_this_actually_uses_are_allowed() -> None:
    for good in ("login-app.example.com", "workspace", "login-site", "a_b-1.2"):
        assert chrome.is_safe_session(good) is True, good


def test_the_default_session_needs_no_profile_of_its_own() -> None:
    assert chrome.profile_for_session(chrome.DEFAULT_SESSION) is None
    assert chrome.profile_for_session(None) is None


def test_a_route_refuses_an_unusable_session_name(monkeypatch, tmp_path) -> None:
    """Refused in words, with nothing touched, rather than coerced into a
    directory the caller did not ask for."""
    client = _client(monkeypatch, tmp_path)
    response = client.post(
        "/browser:ensure",
        json={"session": "../../etc"},
        headers={"X-Lemma-Relay-Token": "token-abc"},
    )
    assert response.status_code == 422, response.text


def test_a_wedged_daemon_is_recognised_from_what_the_cli_says() -> None:
    """There is no exit code that separates a wedged daemon from a page that
    would not load, so the text is all there is to go on."""
    real = (
        "✗ Could not configure browser: Failed to read: Resource temporarily "
        "unavailable (os error 11) (after 5 retries - daemon may be busy or "
        "unresponsive)"
    )
    for complaint in (real, "daemon may be busy", "the daemon is UNRESPONSIVE"):
        assert chrome._daemon_is_wedged(complaint) is True, complaint


def test_an_ordinary_failure_is_not_mistaken_for_a_wedged_daemon() -> None:
    """Restarting the daemon on every failure would take a working browser's
    tabs away from whoever was using it."""
    for complaint in ("net::ERR_NAME_NOT_RESOLVED", "no output", "page not found"):
        assert chrome._daemon_is_wedged(complaint) is False, complaint


def test_the_profile_path_is_not_built_from_the_session_string() -> None:
    """Validating a name and then interpolating it still leaves a path made out
    of a caller's string. A digest cannot carry a separator, a dot, or a way to
    climb out, whatever it was made from."""
    profile = chrome.profile_for_session("login-app.example.com")
    assert profile is not None
    assert "app.example.com" not in profile
    assert profile.startswith("/tmp/lemma-browser/profile-")
    tail = profile.rsplit("-", 1)[-1]
    assert len(tail) == 32 and all(c in "0123456789abcdef" for c in tail)


def test_the_same_session_always_gets_the_same_profile() -> None:
    """A browser has to come back to the directory it locked last time."""
    assert chrome.profile_for_session("login-a.test") == chrome.profile_for_session(
        "login-a.test"
    )
    assert chrome.profile_for_session("login-a.test") != chrome.profile_for_session(
        "login-b.test"
    )


def test_a_refused_vnc_viewer_is_told_which_refusal_it_was(
    monkeypatch, tmp_path
) -> None:
    """The close code has to survive the sandbox wall, or the pane loops.

    A close sent before `accept()` is not a close -- ASGI turns it into a
    rejected handshake, which carries an HTTP status and no close frame. Every
    refusal here then reached the API as one indistinguishable failure, was
    passed on as 1011, and the pane read 1011 as "dropped, retry" and retried
    for ever. Including for "the browser is not running", which is the ordinary
    resting state of an idle workspace and not a failure at all.

    This asserts the shape rather than the sentence: the handshake *succeeds*,
    and the code arrives in the close frame.
    """
    from starlette.websockets import WebSocketDisconnect

    from sandbox_runtime.browser_relay.app import CLOSE_UNAUTHENTICATED

    client = _client(monkeypatch, tmp_path)
    with client.websocket_connect(
        "/vnc?session=../../etc",
        headers={"X-Lemma-Relay-Token": "token-abc"},
    ) as socket:
        with pytest.raises(WebSocketDisconnect) as refused:
            socket.receive_text()
    assert refused.value.code == CLOSE_UNAUTHENTICATED


def test_a_vnc_viewer_without_the_token_is_refused_the_same_way(
    monkeypatch, tmp_path
) -> None:
    """`/vnc` is a second door into the same signed-in browser, so it gets the
    same refusal, not a weaker one because it is newer."""
    from starlette.websockets import WebSocketDisconnect

    from sandbox_runtime.browser_relay.app import CLOSE_UNAUTHENTICATED

    client = _client(monkeypatch, tmp_path)
    with client.websocket_connect("/vnc") as socket:
        with pytest.raises(WebSocketDisconnect) as refused:
            socket.receive_text()
    assert refused.value.code == CLOSE_UNAUTHENTICATED


def test_a_vnc_viewer_with_an_unknown_mode_is_refused(monkeypatch, tmp_path) -> None:
    from starlette.websockets import WebSocketDisconnect

    from sandbox_runtime.browser_relay.app import CLOSE_UNAUTHENTICATED

    client = _client(monkeypatch, tmp_path)
    with client.websocket_connect(
        "/vnc?mode=drive", headers={"X-Lemma-Relay-Token": "token-abc"}
    ) as socket:
        with pytest.raises(WebSocketDisconnect) as refused:
            socket.receive_text()
    assert refused.value.code == CLOSE_UNAUTHENTICATED


def test_a_vnc_viewer_is_refused_when_no_browser_is_running(
    monkeypatch, tmp_path
) -> None:
    """No live Chrome means nothing on `:99` worth showing, and the browser is
    told so with a code it treats as "asleep", not "dropped, retry"."""
    from starlette.websockets import WebSocketDisconnect

    from sandbox_runtime.browser_relay import app as relay_app
    from sandbox_runtime.browser_relay.app import CLOSE_NO_BROWSER
    from sandbox_runtime.browser_relay.chrome import BrowserNotRunning

    async def fake_live_port(session=None):
        raise BrowserNotRunning("no chrome here")

    monkeypatch.setattr(relay_app, "live_port", fake_live_port)
    client = _client(monkeypatch, tmp_path)
    with client.websocket_connect(
        "/vnc", headers={"X-Lemma-Relay-Token": "token-abc"}
    ) as socket:
        with pytest.raises(WebSocketDisconnect) as refused:
            socket.receive_text()
    assert refused.value.code == CLOSE_NO_BROWSER


def test_a_vnc_viewer_is_checked_against_its_own_session_not_the_default(
    monkeypatch, tmp_path
) -> None:
    """The bug this pins: a sign-in's Chrome runs in its own named session,
    not the default one -- `ensure_browser` starts it there, with its own
    profile and its own port. Checking `live_port()` with no session, as the
    route first shipped, asks whether the *default* session's Chrome is
    running and finds nothing, so a person landed on a sign-in page mid-flow
    was told "no browser running" about a browser that was on screen at the
    time. This is only reachable if the check passes for the *named* session
    and still fails for the default one.
    """
    from starlette.websockets import WebSocketDisconnect

    from sandbox_runtime.browser_relay import app as relay_app
    from sandbox_runtime.browser_relay.app import CLOSE_UPSTREAM_GONE
    from sandbox_runtime.browser_relay.chrome import BrowserNotRunning

    async def fake_live_port(session=None):
        if session != "login-example.com":
            raise BrowserNotRunning("no chrome in this session")
        return 12345

    class _RefusingConnect:
        # `websockets.connect(...)` is used as an async context manager, not
        # merely awaited -- this stands in for its shape rather than a bare
        # coroutine. Nothing about reaching websockify is under test here,
        # only that the liveness check passed for the right session: failing
        # fast at the next step, with its own distinct close code, is what
        # proves the refusal above did not fire.
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            raise OSError("no websockify in this test")

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr(relay_app, "live_port", fake_live_port)
    monkeypatch.setattr(relay_app.websockets, "connect", _RefusingConnect)
    client = _client(monkeypatch, tmp_path)
    with client.websocket_connect(
        "/vnc?session=login-example.com",
        headers={"X-Lemma-Relay-Token": "token-abc"},
    ) as socket:
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_text()
    assert closed.value.code == CLOSE_UPSTREAM_GONE


def test_the_vnc_keepalive_touches_the_session_being_watched(
    monkeypatch, tmp_path
) -> None:
    """The bug this pins: the route kept the *default* session warm whatever
    the viewer was actually looking at.

    Watching is not a command, and `agent-browser` retires a browser after two
    idle minutes -- which is the entire reason this loop exists. Pointed at the
    default session, it let the browser actually on screen idle out from under
    the person reading it: a sign-in's `login-<host>`, or a conversation's own
    session. For a sign-in that is worse than a blank panel, because releasing
    runs quiesce and takes the profile -- and the half-finished sign-in -- with
    it. It also kept a browser nobody was watching alive, in a sandbox whose
    memory guard kills on ~220 MB free.
    """
    from starlette.websockets import WebSocketDisconnect

    from sandbox_runtime.browser_relay import app as relay_app
    from sandbox_runtime.browser_relay.app import CLOSE_UPSTREAM_GONE

    async def fake_live_port(session=None):
        return 12345

    kept_warm: list[str] = []

    async def _never_finishes() -> None:
        await asyncio.Event().wait()

    def fake_keepalive_loop(session: str):
        # Recorded where the loop is *created*, not where it first runs: the
        # real one sleeps for a minute before its first touch and this socket
        # is over long before that. Which session it is handed is the whole of
        # what regressed.
        kept_warm.append(session)
        return _never_finishes()

    class _RefusingConnect:
        # Same stand-in as the liveness test above: failing at websockify with
        # its own close code proves the route got past the checks and reached
        # the point where the keepalive has already been started.
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            raise OSError("no websockify in this test")

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr(relay_app, "live_port", fake_live_port)
    monkeypatch.setattr(relay_app, "_keepalive_loop", fake_keepalive_loop)
    monkeypatch.setattr(relay_app.websockets, "connect", _RefusingConnect)
    client = _client(monkeypatch, tmp_path)
    with client.websocket_connect(
        "/vnc?session=login-example.com",
        headers={"X-Lemma-Relay-Token": "token-abc"},
    ) as socket:
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_text()

    assert closed.value.code == CLOSE_UPSTREAM_GONE
    assert kept_warm == ["login-example.com"]


#: Correctly-sized fake RFB client messages, by the protocol's own fixed and
#: header-driven lengths -- a real message, not a plausible-looking prefix of
#: one, is what the smuggling test below needs a legitimate message to be.
_SET_PIXEL_FORMAT_MSG = bytes([0]) + b"\x00" * 19  # type + pad(3) + format(16)
_FRAMEBUFFER_UPDATE_REQUEST_MSG = bytes([3]) + b"\x00" * 9
_POINTER_EVENT_MSG = bytes([5]) + b"\x00" * 5
_KEY_EVENT_MSG = bytes([4]) + b"\x00" * 7
_CLIENT_CUT_TEXT_MSG = bytes([6, 0, 0, 0, 0, 0, 0, 0])  # empty text, length 0

#: The three client-side steps of an RFB handshake, correctly sized -- every
#: test below that exercises `pump_binary` past its first three messages needs
#: these first, or its real messages are themselves mistaken for handshake
#: steps by length and refused before ever reaching the view-safe check.
_FAKE_HANDSHAKE = [b"RFB 003.008\n", bytes([1]), bytes([1])]


class _RecordingUpstream:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def __aiter__(self):
        async def _empty():
            return
            yield  # pragma: no cover - makes this an async generator

        return _empty()

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


def _receiver_over(frames: list[bytes]):
    async def receive_bytes() -> bytes | None:
        if frames:
            return frames.pop(0)
        return None

    return receive_bytes


async def test_a_viewer_watching_over_vnc_cannot_type() -> None:
    """`view` mode forwards only the messages that ask for a picture, and
    drops a `KeyEvent`, a `PointerEvent`, and a `ClientCutText` outright --
    nothing in this product pastes from view mode, so admitting the message
    class on the promise a caller happens not to use it would be exactly the
    allowlist erosion the module warns against."""
    from sandbox_runtime.browser_relay.stream_proxy import pump_binary

    upstream = _RecordingUpstream()
    inbound = [
        *_FAKE_HANDSHAKE,
        _FRAMEBUFFER_UPDATE_REQUEST_MSG,
        _POINTER_EVENT_MSG,
        _KEY_EVENT_MSG,
        _CLIENT_CUT_TEXT_MSG,
        _SET_PIXEL_FORMAT_MSG,
    ]

    await pump_binary(
        upstream,
        mode=VIEW,
        send_bytes=_noop_send,
        receive_bytes=_receiver_over(inbound),
    )
    assert upstream.sent == [
        *_FAKE_HANDSHAKE,
        _FRAMEBUFFER_UPDATE_REQUEST_MSG,
        _SET_PIXEL_FORMAT_MSG,
    ]


async def test_a_view_mode_frame_cannot_smuggle_a_second_message() -> None:
    """The vulnerability this closes: a first version of this filter looked
    only at a frame's first byte, on the assumption that one WebSocket frame
    carries exactly one RFB message. That assumption holds for noVNC; it does
    not hold for whatever a viewer's socket actually is, and nothing stops a
    frame from carrying a legitimate message's bytes followed by a
    `PointerEvent` the byte-0 check never sees. RFB is a byte stream to the
    server on the other end, which has no notion of WebSocket frame
    boundaries -- so a smuggled message reached it exactly as if it had been
    sent openly. The fix drops the *whole* frame rather than forwarding a
    prefix of it, because forwarding the legitimate-looking part is what let
    the rest ride along in the first place."""
    from sandbox_runtime.browser_relay.stream_proxy import pump_binary

    smuggled = _FRAMEBUFFER_UPDATE_REQUEST_MSG + _POINTER_EVENT_MSG
    upstream = _RecordingUpstream()

    await pump_binary(
        upstream,
        mode=VIEW,
        send_bytes=_noop_send,
        receive_bytes=_receiver_over([*_FAKE_HANDSHAKE, smuggled]),
    )
    assert upstream.sent == _FAKE_HANDSHAKE


async def _noop_send(_data: bytes) -> None:
    pass


async def test_a_viewer_driving_over_vnc_can_type() -> None:
    """Driving mode does not run messages through the view-safe check at
    all -- the wheel lease is what gates who may be in this mode, not a
    per-message filter, so a real message's exact byte layout does not
    matter here the way it does for the view-mode tests above."""
    from sandbox_runtime.browser_relay.stream_proxy import pump_binary

    upstream = _RecordingUpstream()
    inbound = [*_FAKE_HANDSHAKE, _POINTER_EVENT_MSG, _KEY_EVENT_MSG]

    await pump_binary(
        upstream,
        mode=CONTROL,
        send_bytes=_noop_send,
        receive_bytes=_receiver_over(inbound),
    )
    assert upstream.sent == [*_FAKE_HANDSHAKE, _POINTER_EVENT_MSG, _KEY_EVENT_MSG]


async def test_the_handshake_passes_through_before_any_view_safe_check() -> None:
    """The regression this pins: a viewer's RFB handshake reply -- the
    ProtocolVersion string, the chosen security type, ClientInit -- carries no
    message-type byte the way every later client-to-server message does, so
    `_view_mode_messages` cannot recognise any of it and, before this was
    handled specially, silently dropped every one of the three steps. The
    viewer's reply never reached the server, which never answered, and the
    connection hung waiting for bytes that were never coming -- discovered by
    running the real relay end to end, not by any of the tests above, none of
    which sent a handshake at all."""
    from sandbox_runtime.browser_relay.stream_proxy import pump_binary

    upstream = _RecordingUpstream()

    await pump_binary(
        upstream,
        mode=VIEW,
        send_bytes=_noop_send,
        receive_bytes=_receiver_over(
            [*_FAKE_HANDSHAKE, _FRAMEBUFFER_UPDATE_REQUEST_MSG]
        ),
    )
    assert upstream.sent == [*_FAKE_HANDSHAKE, _FRAMEBUFFER_UPDATE_REQUEST_MSG]


async def test_a_handshake_step_of_the_wrong_length_is_refused() -> None:
    """Passing the handshake through by length, rather than leaving it
    unfiltered by mode, closes the smuggling window that would otherwise
    reopen here: a step padded with trailing bytes is refused outright, the
    same as an unrecognised message type is once the handshake is behind it,
    rather than having its extra bytes ride along to the server."""
    from sandbox_runtime.browser_relay.stream_proxy import pump_binary

    upstream = _RecordingUpstream()
    padded_client_init = _FAKE_HANDSHAKE[2] + _POINTER_EVENT_MSG

    await pump_binary(
        upstream,
        mode=CONTROL,
        send_bytes=_noop_send,
        receive_bytes=_receiver_over([*_FAKE_HANDSHAKE[:2], padded_client_init]),
    )
    assert upstream.sent == _FAKE_HANDSHAKE[:2]


def test_a_conversation_cannot_rename_the_default_session(monkeypatch) -> None:
    """One relay serves every conversation, so none of them owns "the default".

    The agent's browser script puts its session in the environment with
    `export`, and the shell it runs in is persistent -- so the name outlives the
    command, and the relay, started by an exec into the same sandbox, inherited
    it. It then treated a *conversation's* session as the default one: no
    profile of its own, the port read from the default profile, and every viewer
    told the browser was not running about a browser the line above had just
    talked to.

    Imported fresh under a poisoned environment, because the failure was at
    import time and a constant that is already bound would pass either way.
    """
    import importlib

    monkeypatch.setenv("AGENT_BROWSER_SESSION", "conv-deadbeef")
    chrome = importlib.reload(
        importlib.import_module("sandbox_runtime.browser_relay.chrome")
    )
    try:
        assert chrome.DEFAULT_SESSION == "workspace"
        # The consequence, not just the constant: a session with a name of its
        # own must get a profile of its own, or its port file is read from
        # somebody else's browser.
        assert chrome.profile_for_session("conv-deadbeef") is not None
    finally:
        monkeypatch.undo()
        importlib.reload(chrome)


def test_the_cli_is_told_its_session_in_the_environment_too(monkeypatch) -> None:
    """The flags do not reach the part that starts a cold browser.

    `/usr/local/bin/agent-browser` is a wrapper that runs `start-browser` when
    nothing is up yet, and that script reads `AGENT_BROWSER_SESSION` and
    `AGENT_BROWSER_PROFILE` -- it never sees `--session`. So a relay holding an
    inherited value would pass the flags one session and bootstrap Chrome in
    another's profile.
    """
    from sandbox_runtime.browser_relay.chrome import (
        agent_browser_env,
        profile_for_session,
    )

    monkeypatch.setenv("AGENT_BROWSER_SESSION", "conv-somebody-else")
    monkeypatch.setenv("AGENT_BROWSER_PROFILE", "/tmp/lemma-browser/profile-wrong")

    env = agent_browser_env("login-app.example.com")
    assert env["AGENT_BROWSER_SESSION"] == "login-app.example.com"
    assert env["AGENT_BROWSER_PROFILE"] == profile_for_session("login-app.example.com")
    # The rest of the image's environment is what this is meant to run with.
    assert "PATH" in env


# ---------------------------------------------------------------------------
# The display, and who is allowed to make it big
# ---------------------------------------------------------------------------


def test_the_starting_size_comes_from_the_image(monkeypatch) -> None:
    """One definition, in the thing that actually starts Xvfb at it."""
    monkeypatch.setenv("WORKSPACE_XVFB_SCREEN", "1280x800x24")
    assert chrome.default_display_size() == (1280, 800)
    monkeypatch.delenv("WORKSPACE_XVFB_SCREEN")
    assert chrome.default_display_size() == (1440, 960)
    monkeypatch.setenv("WORKSPACE_XVFB_SCREEN", "nonsense")
    assert chrome.default_display_size() == (1440, 960)


def test_a_viewer_cannot_push_the_display_past_its_starting_size(
    monkeypatch, tmp_path
) -> None:
    """A maximised pane on a large monitor is not a reason to run a 2 GB
    sandbox at 1920x1200 for the rest of its life.

    That was measured to lose an `agent-browser record` part-way through on a
    loaded runner -- 1.67x the pixels for x11vnc to encode and for ffmpeg to
    grab. The framebuffer ceiling is still reachable, but only by an agent
    asking for it on purpose with `set-display-size`.
    """
    monkeypatch.setenv("WORKSPACE_XVFB_SCREEN", "1440x960x24")
    asked: list[tuple[int, int]] = []

    async def fake_resize(width: int, height: int) -> str:
        asked.append((width, height))
        return f"{width}x{height}"

    from sandbox_runtime.browser_relay import app as relay_app

    monkeypatch.setattr(relay_app, "set_display_size", fake_resize)
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/display:resize",
        json={"width": 1900, "height": 1180},
        headers={"X-Lemma-Relay-Token": "token-abc"},
    )

    assert response.status_code == 200
    assert asked == [(1440, 960)]
    # A pane smaller than the cap is passed through untouched.
    client.post(
        "/display:resize",
        json={"width": 900, "height": 700},
        headers={"X-Lemma-Relay-Token": "token-abc"},
    )
    assert asked[-1] == (900, 700)


def test_the_display_can_be_put_back(monkeypatch, tmp_path) -> None:
    """What the last viewer leaving triggers.

    Without it the sandbox kept whichever shape the last pane happened to be
    for the rest of its life, so an agent screenshotting afterwards inherited
    the dimensions of a sidebar it could not see.
    """
    monkeypatch.setenv("WORKSPACE_XVFB_SCREEN", "1440x960x24")
    asked: list[tuple[int, int]] = []

    async def fake_resize(width: int, height: int) -> str:
        asked.append((width, height))
        return f"{width}x{height}"

    from sandbox_runtime.browser_relay import app as relay_app

    monkeypatch.setattr(relay_app, "set_display_size", fake_resize)
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/display:reset", headers={"X-Lemma-Relay-Token": "token-abc"}
    )

    assert response.status_code == 200
    assert response.json()["size"] == "1440x960"
    assert asked == [(1440, 960)]


def test_resetting_the_display_is_behind_the_token(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    assert client.post("/display:reset").status_code == 401
