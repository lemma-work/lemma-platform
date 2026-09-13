"""The relay, driven against a fake Chrome and a fake browser CLI.

The bugs this is guarding against are all ones that produced *no error*: a
blank panel, a stream that stopped after one frame, a start that hung forever
while the browser it was waiting for was up and healthy. So the assertions here
are mostly about what happened rather than about what came back.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest

from sandbox_runtime.browser_relay import chrome, state
from sandbox_runtime.browser_relay.app import TOKEN_PATH, create_app
from sandbox_runtime.browser_relay.screencast import (
    CONTROL,
    VIEW,
    ScreencastSession,
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
# The viewer protocol
# ---------------------------------------------------------------------------


class _RecordingCdp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call(self, method: str, params: dict | None = None) -> dict:
        self.calls.append((method, params or {}))
        return {}

    async def send(self, method: str, params: dict | None = None) -> None:
        self.calls.append((method, params or {}))

    def methods(self) -> list[str]:
        return [method for method, _ in self.calls]


def _frame(session_id: int) -> str:
    return json.dumps(
        {
            "method": "Page.screencastFrame",
            "params": {
                "sessionId": session_id,
                "data": base64.b64encode(b"jpeg").decode(),
                "metadata": {"deviceWidth": 1440, "deviceHeight": 960},
            },
        }
    )


async def test_a_newer_frame_acknowledges_the_one_it_supersedes() -> None:
    """Chrome stops sending until a frame is acknowledged.

    Acknowledging on arrival would pile frames into a slow viewer's socket;
    acknowledging only on the viewer's ack stalls the picture behind one
    dropped message. Latest-wins is what makes a slow link degrade instead of
    freeze.
    """
    cdp = _RecordingCdp()
    viewer = ScreencastSession(cdp, mode=VIEW)

    first = await viewer.handle_cdp_event(_frame(1))
    assert first is not None and first["t"] == "frame"
    assert "Page.screencastFrameAck" not in cdp.methods()

    await viewer.handle_cdp_event(_frame(2))
    acks = [p for m, p in cdp.calls if m == "Page.screencastFrameAck"]
    assert acks == [{"sessionId": 1}], "the superseded frame must be released"


async def test_a_viewer_ack_releases_the_frame_it_names() -> None:
    cdp = _RecordingCdp()
    viewer = ScreencastSession(cdp, mode=VIEW)
    await viewer.handle_cdp_event(_frame(7))

    await viewer.handle_viewer_message(json.dumps({"t": "ack", "seq": 7}))
    assert ("Page.screencastFrameAck", {"sessionId": 7}) in cdp.calls

    # A repeated ack must not release a frame that is no longer outstanding.
    cdp.calls.clear()
    await viewer.handle_viewer_message(json.dumps({"t": "ack", "seq": 7}))
    assert cdp.calls == []


async def test_a_watching_viewer_cannot_type() -> None:
    cdp = _RecordingCdp()
    viewer = ScreencastSession(cdp, mode=VIEW)
    reply = await viewer.handle_viewer_message(
        json.dumps({"t": "input", "event": {"kind": "key", "text": "a"}})
    )
    assert reply is not None and reply["code"] == "read_only"
    assert cdp.calls == [], "nothing reached the browser"


async def test_a_driving_viewer_can_type_and_click() -> None:
    cdp = _RecordingCdp()
    viewer = ScreencastSession(cdp, mode=CONTROL)
    await viewer.handle_viewer_message(
        json.dumps(
            {"t": "input", "event": {"kind": "key", "type": "keyDown", "text": "a"}}
        )
    )
    await viewer.handle_viewer_message(
        json.dumps(
            {"t": "input", "event": {"kind": "mouse", "type": "mousePressed", "x": 4}}
        )
    )
    assert cdp.methods() == [
        "Input.dispatchKeyEvent",
        "Input.dispatchMouseEvent",
    ]


async def test_the_viewer_protocol_cannot_express_anything_else() -> None:
    """The reason this is not a CDP allowlist.

    A viewer has four verbs. There is no `Runtime.evaluate` to forget to refuse,
    no `Page.navigate` to leave off a list, and no `Network.getAllCookies` that
    a future Chrome release quietly renames past a prefix match.
    """
    cdp = _RecordingCdp()
    viewer = ScreencastSession(cdp, mode=CONTROL)

    for attempt in (
        {"t": "input", "event": {"kind": "evaluate", "expression": "document.cookie"}},
        {"t": "cdp", "method": "Runtime.evaluate"},
        {"t": "input", "event": {"kind": "navigate", "url": "http://evil.test"}},
    ):
        reply = await viewer.handle_viewer_message(json.dumps(attempt))
        assert reply is not None and reply["t"] == "error"
    assert cdp.calls == [], "nothing reached the browser"


async def test_an_unreadable_message_is_answered_not_dropped() -> None:
    viewer = ScreencastSession(_RecordingCdp(), mode=CONTROL)
    reply = await viewer.handle_viewer_message("{not json")
    assert reply is not None and reply["code"] == "unreadable"


async def test_only_the_top_frame_counts_as_navigation() -> None:
    """An ad iframe navigating is not the person's sign-in completing."""
    viewer = ScreencastSession(_RecordingCdp(), mode=VIEW)
    child = json.dumps(
        {
            "method": "Page.frameNavigated",
            "params": {"frame": {"url": "http://ads.test", "parentId": "1"}},
        }
    )
    assert await viewer.handle_cdp_event(child) is None

    top = json.dumps(
        {
            "method": "Page.frameNavigated",
            "params": {"frame": {"url": "https://app.example.com/home"}},
        }
    )
    assert await viewer.handle_cdp_event(top) == {
        "t": "navigated",
        "url": "https://app.example.com/home",
    }


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
        "/state:clear",
        "/session",
    } <= served
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
        ("/state:clear", {}),
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
