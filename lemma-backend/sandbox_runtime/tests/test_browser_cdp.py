from __future__ import annotations

import asyncio
import json

import pytest

from sandbox_runtime.workspace.app import cdp_message_is_allowed
from sandbox_runtime.workspace.browser_cdp import (
    BrowserNotRunning,
    live_port,
    page_socket_url,
    recorded_port,
)


class _FakeStdout:
    """A pipe that yields lines and then *stays open*, like the real one.

    The daemon `start-browser` leaves running inherits the CLI's stdout, so it
    never reaches EOF. A double that closes after its last line would let the
    EOF-waiting bug back in without a single test going red.
    """

    def __init__(self, lines: list[bytes], *, ends: bool) -> None:
        self._lines = list(lines)
        self._ends = ends

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        if self._ends:
            return b""
        await asyncio.Event().wait()  # the pipe the daemon is holding open
        raise AssertionError("unreachable")


class _FakeProcess:
    def __init__(self, lines: list[bytes], returncode: int, ends: bool) -> None:
        self.stdout = _FakeStdout(lines, ends=ends)
        # None means running, as it does on the real thing. The kill guard turns
        # on exactly this, so a double that reports "already exited" would let a
        # wedged CLI leak without failing a test.
        self.returncode: int | None = None
        self._exit_code = returncode
        self._ends = ends
        self.killed = False

    async def wait(self) -> int:
        if not self._ends:
            await asyncio.Event().wait()  # a CLI that never exits
        self.returncode = self._exit_code
        return self._exit_code

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class _FakeAsyncio:
    """Stands in for the `asyncio` module `browser_cdp` reaches for.

    Only `create_subprocess_exec` is replaced; `wait_for`, `subprocess` and
    `TimeoutError` are the real thing, so a timeout in a test is a real timeout.
    """

    def __init__(
        self,
        *,
        stdout: bytes = b"",
        returncode: int = 0,
        lines: list[bytes] | None = None,
        ends: bool = True,
    ) -> None:
        self._lines = lines if lines is not None else ([stdout] if stdout else [])
        self._returncode = returncode
        self._ends = ends
        self.subprocess = asyncio.subprocess
        self.TimeoutError = asyncio.TimeoutError
        self.wait_for = asyncio.wait_for
        self.last: _FakeProcess | None = None

    async def create_subprocess_exec(self, *argv, **kwargs) -> _FakeProcess:
        assert argv[0].endswith("agent-browser"), argv
        assert argv[1:3] == ("get", "cdp-url"), argv
        self.last = _FakeProcess(self._lines, self._returncode, self._ends)
        return self.last


def _message(method: str) -> str:
    return json.dumps({"id": 1, "method": method, "params": {}})


@pytest.mark.parametrize(
    "method",
    [
        "Input.dispatchKeyEvent",
        "Input.dispatchMouseEvent",
        "Input.insertText",
        "Page.enable",
        "Page.startScreencast",
        "Page.stopScreencast",
        "Page.screencastFrameAck",
        "Page.getLayoutMetrics",
    ],
)
def test_a_viewer_may_watch_and_type(method: str) -> None:
    assert cdp_message_is_allowed(_message(method)) is True


@pytest.mark.parametrize(
    "method",
    [
        # Reads every cookie in the session.
        "Network.getAllCookies",
        "Storage.getCookies",
        # Runs arbitrary script in the logged-in page.
        "Runtime.evaluate",
        "Runtime.callFunctionOn",
        # Points the browser somewhere the person did not choose — a page that
        # harvests the session it is being shown.
        "Page.navigate",
        "Page.navigateToHistoryEntry",
        # Opens or closes the browser out from under the viewer.
        "Target.createTarget",
        "Target.closeTarget",
        "Browser.close",
        # Reads the disk of the machine the browser runs on.
        "IO.read",
        "DOM.getDocument",
    ],
)
def test_a_viewer_may_not_take_the_session(method: str) -> None:
    """Raw CDP is total control, so anything a browser tab holds is something an
    XSS on that page holds too."""
    assert cdp_message_is_allowed(_message(method)) is False


def test_page_is_matched_exactly_not_by_prefix() -> None:
    """`Page.` alone would admit `Page.navigate`, which is the whole risk."""
    assert cdp_message_is_allowed(_message("Page.startScreencast")) is True
    assert cdp_message_is_allowed(_message("Page.navigate")) is False


@pytest.mark.parametrize(
    "raw",
    ["not json", "", "[]", '{"id": 1}', '{"method": 42}', "null"],
)
def test_what_cannot_be_read_cannot_be_judged(raw: str) -> None:
    """The safe reading of "I don't know what this is" is no."""
    assert cdp_message_is_allowed(raw) is False


def test_without_a_running_browser_there_is_nothing_to_attach_to(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from sandbox_runtime.workspace import browser_cdp

    monkeypatch.setattr(
        browser_cdp, "_ACTIVE_PORT_FILE", tmp_path / "DevToolsActivePort"
    )
    with pytest.raises(BrowserNotRunning):
        recorded_port()


def test_the_port_is_read_from_chromes_own_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Chrome picks the port; forcing one breaks `agent-browser`, which waits
    for this very file to appear."""
    from sandbox_runtime.workspace import browser_cdp

    active = tmp_path / "DevToolsActivePort"
    # Second line is the browser-level path, which a page client must not use.
    active.write_text("44043\n/devtools/browser/abc-123\n")
    monkeypatch.setattr(browser_cdp, "_ACTIVE_PORT_FILE", active)

    assert recorded_port() == 44043
    assert page_socket_url("t1", port=44043) == "ws://127.0.0.1:44043/devtools/page/t1"


@pytest.mark.asyncio
async def test_a_port_chrome_left_behind_is_not_a_running_browser(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Chrome does not remove `DevToolsActivePort` on the way out, and it leaves
    often -- `agent-browser` retires it after two idle minutes.

    This is not hypothetical. Believing that file cost a long debugging session:
    a viewer asking to watch a browser that had timed out got a connection error
    rather than "not running", which surfaced as a 500 and, to the person
    clicking, as an unexplained failure they hit every single time.
    """
    from sandbox_runtime.workspace import browser_cdp

    active = tmp_path / "DevToolsActivePort"
    active.write_text("44043\n/devtools/browser/abc-123\n")
    monkeypatch.setattr(browser_cdp, "_ACTIVE_PORT_FILE", active)

    async def nothing_listening(port: int) -> bool:
        return False

    monkeypatch.setattr(browser_cdp, "_answers_on", nothing_listening)

    # The file still parses, and that is exactly the trap.
    assert recorded_port() == 44043
    with pytest.raises(BrowserNotRunning):
        await live_port()


@pytest.mark.asyncio
async def test_a_recorded_port_that_answers_is_the_live_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from sandbox_runtime.workspace import browser_cdp

    active = tmp_path / "DevToolsActivePort"
    active.write_text("44043\n")
    monkeypatch.setattr(browser_cdp, "_ACTIVE_PORT_FILE", active)

    async def answers(port: int) -> bool:
        return port == 44043

    monkeypatch.setattr(browser_cdp, "_answers_on", answers)

    assert await browser_cdp.live_port() == 44043


@pytest.mark.asyncio
async def test_starting_the_browser_reports_the_port_it_actually_landed_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A restarted Chrome picks a *new* port, so the answer cannot come from the
    stale file -- it has to come from the thing that did the starting."""
    from sandbox_runtime.workspace import browser_cdp

    active = tmp_path / "DevToolsActivePort"
    active.write_text("42529\n")  # where Chrome was, not where it is
    monkeypatch.setattr(browser_cdp, "_ACTIVE_PORT_FILE", active)
    monkeypatch.setattr(
        browser_cdp,
        "asyncio",
        _FakeAsyncio(
            stdout=b"ws://127.0.0.1:43729/devtools/browser/0fe01af9-7046\n",
            returncode=0,
        ),
    )

    assert await browser_cdp.ensure_port() == 43729


@pytest.mark.asyncio
async def test_a_browser_that_will_not_start_is_reported_as_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rather than as a crash: a sandbox too starved to hold a browser is a
    state a viewer should be told about, not a defect."""
    from sandbox_runtime.workspace import browser_cdp

    monkeypatch.setattr(
        browser_cdp,
        "asyncio",
        _FakeAsyncio(stdout=b"", returncode=1),
    )

    with pytest.raises(BrowserNotRunning):
        await browser_cdp.ensure_port()


@pytest.mark.asyncio
async def test_the_cli_stderr_is_not_carried_to_the_viewer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It is reported to a person watching a browser, and it is not theirs to
    read."""
    from sandbox_runtime.workspace import browser_cdp

    monkeypatch.setattr(
        browser_cdp,
        "asyncio",
        _FakeAsyncio(
            lines=[b"/home/appuser/.agent-browser/secret-path exploded\n"],
            returncode=1,
        ),
    )

    with pytest.raises(BrowserNotRunning) as caught:
        await browser_cdp.ensure_port()
    assert "secret-path" not in str(caught.value)


@pytest.mark.asyncio
async def test_the_browser_is_started_through_the_wrapper_not_the_raw_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runtime's PATH is not the agent's PATH.

    Two things answer to `agent-browser`: the raw npm binary, and the
    `lemma-node-tool` wrapper that runs `start-browser` first. An agent shell
    finds the wrapper; this process finds the binary, because
    `/opt/lemma-node/node_modules/.bin` comes earlier on its PATH. In a sandbox
    where nothing has used the browser yet, the raw binary fails with `config
    file not found` and a takeover has nothing to show.
    """
    from sandbox_runtime.workspace import browser_cdp

    invoked: list[str] = []

    class _Recording(_FakeAsyncio):
        async def create_subprocess_exec(self, *argv, **kwargs):
            invoked.append(argv[0])
            return await super().create_subprocess_exec(*argv, **kwargs)

    monkeypatch.setattr(
        browser_cdp,
        "asyncio",
        _Recording(stdout=b"ws://127.0.0.1:43967/devtools/browser/x\n", returncode=0),
    )
    monkeypatch.setattr(browser_cdp.Path, "exists", lambda self: True)

    await browser_cdp.ensure_port()

    assert invoked == ["/usr/local/bin/agent-browser"]


@pytest.mark.asyncio
async def test_a_pipe_the_daemon_holds_open_does_not_hang_the_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`start-browser` leaves Xvfb and the browser daemon running, and they
    inherit the CLI's stdout -- so it never reaches EOF.

    Anything that waits for the output to *end* waits forever. That is the bug
    that made a takeover fail with an empty panel while the browser it was
    waiting for was up and healthy the whole time, and running the same command
    under `docker exec` hid it completely, because nothing there captured the
    output. So: the port is read from a line, and the never-closing pipe below
    is what keeps that honest.
    """
    from sandbox_runtime.workspace import browser_cdp

    monkeypatch.setattr(
        browser_cdp,
        "asyncio",
        _FakeAsyncio(
            lines=[
                b"bootstrapping browser\n",
                b"ws://127.0.0.1:41805/devtools/browser/abc\n",
            ],
            ends=False,  # the daemon still holds it
        ),
    )
    monkeypatch.setattr(browser_cdp, "_START_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(browser_cdp, "_REAP_TIMEOUT_SECONDS", 0.05)

    assert await browser_cdp.ensure_port() == 41805


@pytest.mark.asyncio
async def test_a_cli_that_never_answers_is_given_up_on_and_killed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bound exists to stop a wedged start hanging forever, and the process
    must not outlive the answer."""
    from sandbox_runtime.workspace import browser_cdp

    fake = _FakeAsyncio(lines=[], ends=False)
    monkeypatch.setattr(browser_cdp, "asyncio", fake)
    monkeypatch.setattr(browser_cdp, "_START_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(browser_cdp, "_REAP_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(BrowserNotRunning):
        await browser_cdp.ensure_port()
    assert fake.last is not None and fake.last.killed
