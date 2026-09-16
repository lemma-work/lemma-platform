"""The relay, driven against a fake Chrome and a fake browser CLI.

The bugs this is guarding against are all ones that produced *no error*: a
blank panel, a stream that stopped after one frame, a start that hung forever
while the browser it was waiting for was up and healthy. So the assertions here
are mostly about what happened rather than about what came back.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from sandbox_runtime.browser_relay import chrome, state
from sandbox_runtime.browser_relay.app import TOKEN_PATH, create_app
from sandbox_runtime.browser_relay.stream_proxy import (
    CONTROL,
    VIEW,
    viewer_message_allowed,
)

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
# What a viewer may send
# ---------------------------------------------------------------------------
#
# The frame protocol itself is `agent-browser`'s, not ours -- we proxy its
# session-scoped stream rather than driving CDP. What is still ours, and so
# what is tested here, is the one rule the stream server does not know about:
# a viewer who asked to watch may not type.


def _allowed(message: dict, *, mode: str) -> bool:
    ok, _ = viewer_message_allowed(json.dumps(message), mode=mode)
    return ok


def _refusal(message: dict, *, mode: str) -> dict | None:
    _, refusal = viewer_message_allowed(json.dumps(message), mode=mode)
    return refusal


def test_watching_cannot_type() -> None:
    """The stream server takes input from whoever connects.

    It has no notion of a read-only viewer, so `view` mode is enforced here --
    the only place that knows which mode was asked for. Without it, "watch"
    and "drive" would be the same socket with a different label.
    """
    for kind in ("input_mouse", "input_keyboard", "input_touch"):
        assert _allowed({"type": kind}, mode=VIEW) is False
        assert _refusal({"type": kind}, mode=VIEW)["code"] == "read_only"


def test_driving_can_type_and_touch() -> None:
    """Touch included: it is what makes a sign-in work on a phone, and it is
    something the hand-rolled screencast never had."""
    for kind in ("input_mouse", "input_keyboard", "input_touch"):
        assert _allowed({"type": kind}, mode=CONTROL) is True


def test_pacing_and_acks_are_allowed_to_a_watcher() -> None:
    """Capping your own frame rate is not driving the page."""
    for kind in ("config", "ack", "screencast_start", "screencast_stop"):
        assert _allowed({"type": kind}, mode=VIEW) is True


def test_anything_outside_the_vocabulary_is_refused_in_words() -> None:
    """Matched against a set, not a prefix.

    `input_*` as a prefix test would silently admit whatever the next release
    of the CLI adds under that name. And a message dropped in silence looks to
    a client exactly like a browser that has stopped.
    """
    assert _allowed({"type": "input_something_new"}, mode=CONTROL) is False
    assert _refusal({"type": "Runtime.evaluate"}, mode=CONTROL)["code"] == (
        "unknown_message"
    )


def test_unreadable_messages_are_never_forwarded() -> None:
    ok, refusal = viewer_message_allowed("{not json", mode=CONTROL)
    assert ok is False
    assert refusal["code"] == "unreadable"
    ok, _ = viewer_message_allowed('"a string"', mode=CONTROL)
    assert ok is False


def test_the_stream_url_paces_from_the_opening_frame() -> None:
    """`pacing` and `maxFps` go on the URL because the CLI's own help says that
    is the only way to cover the first frame; a config message arrives too
    late to pace it."""
    url = chrome.stream_socket_url(41234, max_fps=15)
    assert url.startswith("ws://127.0.0.1:41234/?")
    assert "pacing=ack" in url
    assert "maxFps=15" in url


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
# Session state
# ---------------------------------------------------------------------------


def test_a_login_session_is_named_for_its_site() -> None:
    """Scoping by construction: a capture from this session can only contain
    what was signed in to in it."""
    assert state.session_for_domain("example.com") == "login-example.com"
    assert state.session_for_domain("EXAMPLE.com") == "login-example.com"
    # A hostile domain cannot escape into a shell argument or a path.
    assert "/" not in state.session_for_domain("a/../../etc/passwd")
    assert ";" not in state.session_for_domain("a;rm -rf /")


def test_the_state_directory_is_never_the_durable_volume() -> None:
    """A saved session under /workspace would outlive the run that captured it
    and be waiting for whatever ran next."""
    assert not str(state._STATE_DIR).startswith("/workspace")
    assert str(state._STATE_DIR).startswith("/tmp/")


async def test_a_saved_session_leaves_no_file_behind(monkeypatch, tmp_path) -> None:
    written: dict[str, Path] = {}

    async def fake_run(argv: list[str]) -> tuple[int, str]:
        path = Path(argv[-1])
        written["path"] = path
        path.write_text(json.dumps({"cookies": [{"name": "s", "value": "v"}]}))
        return 0, ""

    monkeypatch.setattr(state, "_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(state, "_run", fake_run)

    saved = await state.save_session(session="login-example.com")
    assert saved == {"cookies": [{"name": "s", "value": "v"}]}
    assert not written["path"].exists(), "the plaintext session must not persist"


async def test_an_oversized_session_is_refused(monkeypatch, tmp_path) -> None:
    async def fake_run(argv: list[str]) -> tuple[int, str]:
        Path(argv[-1]).write_text(json.dumps({"junk": "x" * (3 * 1024 * 1024)}))
        return 0, ""

    monkeypatch.setattr(state, "_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(state, "_run", fake_run)

    with pytest.raises(state.StateOperationFailed, match="larger than"):
        await state.save_session(session="login-example.com")


async def test_a_loaded_session_is_staged_and_removed(monkeypatch, tmp_path) -> None:
    seen: dict[str, object] = {}

    async def fake_run(argv: list[str]) -> tuple[int, str]:
        path = Path(argv[-1])
        seen["path"] = path
        seen["content"] = json.loads(path.read_text())
        seen["mode"] = path.stat().st_mode & 0o777
        return 0, ""

    monkeypatch.setattr(state, "_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(state, "_run", fake_run)

    await state.load_session({"cookies": []}, session="login-example.com")
    assert seen["content"] == {"cookies": []}
    assert seen["mode"] == 0o600, "readable by its owner and nobody else"
    assert not Path(seen["path"]).exists()  # type: ignore[arg-type]


def test_the_relay_serves_only_what_it_means_to() -> None:
    """A route added here is a door into a signed-in browser, so the set is
    asserted rather than assumed."""
    app = create_app()
    served = {getattr(r, "path", "") for r in app.routes}
    assert {
        "/health",
        "/targets",
        "/browser:ensure",
        "/state:save",
        "/state:load",
        "/session",
    } <= served
    # Asserted as an equality on the state routes, not a subset: `/state:clear`
    # was here and nothing ever called it, all the way down through the client
    # and the service to `clear_session`. A route into a signed-in browser that
    # no product path uses is surface for free.
    assert {p for p in served if p.startswith("/state")} == {
        "/state:save",
        "/state:load",
    }
    assert not {p for p in served if p.startswith("/cdp")}


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


def test_every_state_route_is_behind_the_token(monkeypatch, tmp_path) -> None:
    """These read and write signed-in sessions; none may be reachable openly."""
    client = _client(monkeypatch, tmp_path)
    for path, body in (
        ("/browser:ensure", {}),
        ("/state:save", {}),
        ("/state:load", {"state": {}}),
    ):
        assert client.post(path, json=body).status_code == 401, path


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


def test_a_derived_name_is_safe_by_construction() -> None:
    """`session_for_domain` already strips; this is belt to that brace."""
    for hostile in ("a/../../etc/passwd", "a;rm -rf /", "../..", "x\x00y"):
        assert chrome.is_safe_session(state.session_for_domain(hostile)) is True


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


async def test_input_for_a_dead_stream_ends_the_socket_rather_than_vanishing() -> None:
    """A person typing into a picture has to be told.

    The old CDP path wrapped its dispatch in `suppress(Exception)`: with the
    socket to Chrome gone, clicks and keystrokes vanished with nothing on screen
    and nothing in the log. Forwarding to a dead stream now raises out of the
    pump, the viewer's socket closes, and the pane says the connection dropped
    and reconnects -- which is the truth.
    """
    from sandbox_runtime.browser_relay.stream_proxy import pump

    class _DeadStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(3600)

        async def send(self, _raw):
            raise ConnectionResetError("the stream went away")

    sent: list[str] = []
    incoming = [json.dumps({"type": "input_mouse", "eventType": "mousePressed"})]

    async def receive_text():
        return incoming.pop(0) if incoming else None

    await pump(
        _DeadStream(), mode=CONTROL, send_text=sent.append, receive_text=receive_text
    )

    # The pump returned rather than hanging: the caller closes the socket, and
    # nothing was quietly dropped on the floor.
    assert sent == []


async def test_a_refusal_reaches_the_viewer_without_touching_the_stream() -> None:
    """Refused input is answered, not dropped."""
    from sandbox_runtime.browser_relay.stream_proxy import pump

    forwarded: list[str] = []

    class _Stream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def send(self, raw):
            forwarded.append(raw)

    sent: list[str] = []
    incoming = [json.dumps({"type": "input_keyboard", "eventType": "keyDown"})]

    async def receive_text():
        return incoming.pop(0) if incoming else None

    await pump(_Stream(), mode=VIEW, send_text=sent.append, receive_text=receive_text)

    assert forwarded == []
    assert json.loads(sent[0])["code"] == "read_only"


def test_a_refused_viewer_is_told_which_refusal_it_was(monkeypatch, tmp_path) -> None:
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
        "/session?session=../../etc",
        headers={"X-Lemma-Relay-Token": "token-abc"},
    ) as socket:
        with pytest.raises(WebSocketDisconnect) as refused:
            socket.receive_text()
    assert refused.value.code == CLOSE_UNAUTHENTICATED


def test_a_viewer_without_the_token_is_refused_the_same_way(
    monkeypatch, tmp_path
) -> None:
    """The socket is the one route a browser opens, so it is the one that has to
    refuse in a code rather than in a status nobody can read."""
    from starlette.websockets import WebSocketDisconnect

    from sandbox_runtime.browser_relay.app import CLOSE_UNAUTHENTICATED

    client = _client(monkeypatch, tmp_path)
    with client.websocket_connect("/session") as socket:
        with pytest.raises(WebSocketDisconnect) as refused:
            socket.receive_text()
    assert refused.value.code == CLOSE_UNAUTHENTICATED


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


def test_one_viewer_leaving_does_not_release_another_viewers_wheel(
    monkeypatch, tmp_path
) -> None:
    """The lease says who holds it, not merely that somebody does.

    Two people can have the same session open -- a second tab, a phone
    alongside a laptop, a reconnect that overlaps its own close. The lease was
    a file whose existence was the whole signal, so whichever socket closed
    first deleted it, and the one still driving lost the wheel without being
    told. The agent's script reads that file to decide whether to yield, so
    what followed was a command typed into a page somebody was using.
    """
    from sandbox_runtime.browser_relay import app as relay_app

    monkeypatch.setattr(relay_app, "_WHEEL_DIR", tmp_path / "wheel")

    first = relay_app._take_the_wheel("conv-abc")
    second = relay_app._take_the_wheel("conv-abc")
    assert first is not None and second is not None
    assert first.token != second.token

    relay_app._release_the_wheel(first)
    assert relay_app.wheel_path("conv-abc").exists(), "the second viewer still holds it"

    relay_app._release_the_wheel(second)
    assert not relay_app.wheel_path("conv-abc").exists()


# ---------------------------------------------------------------------------
# The size of the page, which a frame does not say
# ---------------------------------------------------------------------------


class _FakeCdp:
    """A page target's debugger socket, answering one method."""

    def __init__(self, reply: dict) -> None:
        self.reply = reply
        self.sent: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, raw: str) -> None:
        self.sent.append(raw)

    async def recv(self) -> str:
        # An event first, on the same socket, because CDP interleaves them --
        # and taking the first message rather than matching the id is how this
        # would read whatever the page happened to fire.
        if len(self.sent) == 1:
            self.sent.append("read")
            return json.dumps({"method": "Page.frameNavigated", "params": {}})
        return json.dumps(self.reply)


def _fake_targets(monkeypatch, targets: list[dict]) -> None:
    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict]:
            return targets

    class _Client:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, _url: str) -> "_Response":
            return _Response()

    monkeypatch.setattr(chrome.httpx, "AsyncClient", _Client)


async def test_the_viewport_is_the_pages_css_pixels_not_the_devices(
    monkeypatch,
) -> None:
    """The measurement the pane cannot make for itself.

    A click arrives as a fraction of the picture and has to be scaled up into
    the page before the stream will dispatch it anywhere. `layoutViewport` is
    device pixels; `cssLayoutViewport` is the space input is in, and on a page
    with a scale factor the two differ -- which is the same class of mistake
    this function exists to end.
    """
    _fake_targets(
        monkeypatch,
        [{"type": "page", "id": "T1", "webSocketDebuggerUrl": "ws://127.0.0.1:1/T1"}],
    )
    cdp = _FakeCdp(
        {
            "id": 1,
            "result": {
                "layoutViewport": {"clientWidth": 1280, "clientHeight": 720},
                "cssLayoutViewport": {"clientWidth": 1050, "clientHeight": 797},
            },
        }
    )
    monkeypatch.setattr(chrome.websockets, "connect", lambda _url: cdp)

    assert await chrome.viewport_size(port=9222, target_id="T1") == (1050, 797)
    assert json.loads(cdp.sent[0])["method"] == "Page.getLayoutMetrics"


async def test_a_browser_that_will_not_say_leaves_the_pane_to_fall_back(
    monkeypatch,
) -> None:
    """`None`, not a raise and not a zero.

    The pane's fallback is the picture's own pixels -- off by the scale factor,
    which is what it did before anybody asked. Raising here would turn a pane
    that works imperfectly into one that does not attach at all, and a zero
    would put every click in the top-left corner.
    """
    _fake_targets(monkeypatch, [{"type": "page", "id": "T1"}])

    assert await chrome.viewport_size(port=9222, target_id="T1") is None


async def test_device_pixels_are_not_handed_back_as_css_pixels(monkeypatch) -> None:
    """`layoutViewport` is device pixels, and there is no fallback to it.

    Handing those back would have the pane scale confidently by the wrong
    number -- the same class of mistake this function exists to end. `None`
    instead, which leaves it on the picture's pixels: wrong by a factor it at
    least has a comment about.
    """
    _fake_targets(
        monkeypatch,
        [{"type": "page", "id": "T1", "webSocketDebuggerUrl": "ws://127.0.0.1:1/T1"}],
    )
    monkeypatch.setattr(
        chrome.websockets,
        "connect",
        lambda _url: _FakeCdp(
            {
                "id": 1,
                "result": {
                    "layoutViewport": {"clientWidth": 1280, "clientHeight": 720}
                },
            }
        ),
    )

    assert await chrome.viewport_size(port=9222, target_id="T1") is None


async def test_a_tab_that_has_never_laid_out_does_not_end_the_search(
    monkeypatch,
) -> None:
    """The stream shows the session's active tab, and nothing here knows which.

    `target_id` is a check that the caller and the stream agree about which
    *browser*; it says nothing about which tab is on screen. So the named tab
    can be one that has never rendered, which answers with zeros -- and
    stopping there would leave the pane with no measurement at all when a
    sibling in the same window could have said. Every page target in a session
    is a tab in one window, so the one that answers is reporting the window the
    stream is showing.
    """
    _fake_targets(
        monkeypatch,
        [
            {"type": "page", "id": "T1", "webSocketDebuggerUrl": "ws://127.0.0.1:1/T1"},
            {"type": "page", "id": "T2", "webSocketDebuggerUrl": "ws://127.0.0.1:1/T2"},
        ],
    )
    replies = {
        "ws://127.0.0.1:1/T1": {
            "id": 1,
            "result": {"cssLayoutViewport": {"clientWidth": 0, "clientHeight": 0}},
        },
        "ws://127.0.0.1:1/T2": {
            "id": 1,
            "result": {"cssLayoutViewport": {"clientWidth": 1050, "clientHeight": 797}},
        },
    }
    asked: list[str] = []

    def _connect(url: str):
        asked.append(url)
        return _FakeCdp(replies[url])

    monkeypatch.setattr(chrome.websockets, "connect", _connect)

    assert await chrome.viewport_size(port=9222, target_id="T1") == (1050, 797)
    assert asked == ["ws://127.0.0.1:1/T1", "ws://127.0.0.1:1/T2"], asked
