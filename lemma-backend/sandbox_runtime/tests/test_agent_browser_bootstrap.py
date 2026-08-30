"""`agent-browser` has to work whether or not `start-browser` ran first.

The browser skill says to call `start-browser` once before anything else, and
that is the flow that was tested. An agent that goes straight for
`agent-browser` -- the obvious thing to type -- got one of two errors instead:

    ⚠ config file not found: /tmp/lemma-browser/config.json
    Missing X server or $DISPLAY

Both mean "start-browser has not run yet". Neither says so. From inside the
sandbox the reasonable conclusion is that the browser tooling is broken, and the
fallback a model reaches for is installing Playwright and downloading its own
Chromium -- into a 2 GB sandbox, to do what the browser already sitting there
does. That happened in a real transcript.

The wrapper on `PATH` now brings the browser up itself. These tests drive that
wrapper directly with a stub `start-browser` on `PATH`, because what has to be
right is the decision -- when to bootstrap, and just as importantly when not to.
"""

from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess

import pytest

# Bound display sockets, closed and unlinked after each test so a second run
# does not find the first one's address already taken.
_SOCKETS: list[socket.socket] = []


@pytest.fixture(autouse=True)
def _release_display_sockets():
    yield
    while _SOCKETS:
        listener = _SOCKETS.pop()
        address = listener.getsockname()
        listener.close()
        Path(address).unlink(missing_ok=True)


SCRIPT = Path(__file__).resolve().parents[2] / "sandbox-images/scripts/lemma-node-tool"


def _workspace(tmp_path: Path, *, config: bool, display: bool) -> dict[str, str]:
    """A sandbox in a given state, with `start-browser` stubbed to leave a mark."""
    binaries = tmp_path / "bin"
    binaries.mkdir()
    marker = tmp_path / "start-browser-ran"
    start_browser = binaries / "start-browser"
    start_browser.write_text(
        f'#!/bin/sh\necho "$LEMMA_BROWSER_BOOTSTRAP" > "{marker}"\n'
    )
    start_browser.chmod(0o755)

    # The entrypoint the wrapper execs. Present so the wrapper reaches its own
    # exit rather than the "not installed" path, and echoing its arguments so a
    # test can prove the caller's command survived the bootstrap.
    node_project = tmp_path / "node"
    entrypoint = (
        node_project
        / "node_modules/.pnpm/agent-browser@0/node_modules/agent-browser/bin"
    )
    entrypoint.mkdir(parents=True)
    (entrypoint / "agent-browser.js").write_text("")
    node = binaries / "fake-node"
    node.write_text('#!/bin/sh\nshift\necho "agent-browser $*"\n')
    node.chmod(0o755)

    config_path = tmp_path / "config.json"
    if config:
        config_path.write_text("{}")

    # A display is "up" exactly when a socket exists at the path X clients look
    # for, which is what the wrapper tests. An unusual display number keeps this
    # away from any real X server on the machine running the tests.
    display_number = 77 if display else 78
    if display:
        os.makedirs("/tmp/.X11-unix", exist_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(f"/tmp/.X11-unix/X{display_number}")
        _SOCKETS.append(listener)

    return {
        "PATH": f"{binaries}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "LEMMA_NODE_BINARY": str(node),
        "LEMMA_NODE_PROJECT": str(node_project),
        "AGENT_BROWSER_CONFIG": str(config_path),
        "DISPLAY": f":{display_number}",
        "_MARKER": str(marker),
    }


def _run(environment: dict[str, str], *arguments: str) -> subprocess.CompletedProcess:
    invoked_as = Path(environment["HOME"]) / "bin-agent-browser"
    invoked_as.write_text(SCRIPT.read_text())
    invoked_as.chmod(0o755)
    # Invoked under the name the image symlinks, since the wrapper switches on
    # its own basename.
    linked = invoked_as.parent / "agent-browser"
    linked.write_text(SCRIPT.read_text())
    linked.chmod(0o755)
    return subprocess.run(
        [str(linked), *arguments],
        capture_output=True,
        text=True,
        env={k: v for k, v in environment.items() if not k.startswith("_")},
    )


def _bootstrapped(environment: dict[str, str]) -> bool:
    return Path(environment["_MARKER"]).exists()


def test_a_command_with_no_browser_running_starts_one(tmp_path: Path) -> None:
    """The bug, directly: this used to fail instead of starting the browser."""
    environment = _workspace(tmp_path, config=False, display=False)
    result = _run(environment, "open", "https://example.com")

    assert _bootstrapped(environment), (
        "agent-browser ran with no browser up and did not start one"
    )
    # The caller's command still runs, and still carries its arguments.
    assert "open https://example.com" in result.stdout
    assert result.returncode == 0


def test_a_missing_display_alone_is_enough_to_bootstrap(tmp_path: Path) -> None:
    """Config written but Xvfb gone is the second failure, and the same cause."""
    environment = _workspace(tmp_path, config=True, display=False)
    _run(environment, "snapshot", "-i")
    assert _bootstrapped(environment)


def test_a_browser_already_up_is_not_restarted(tmp_path: Path) -> None:
    """Bootstrapping a live session would reopen a blank page over the
    agent's work."""
    environment = _workspace(tmp_path, config=True, display=True)

    _run(environment, "snapshot", "-i")

    assert not _bootstrapped(environment), (
        "a running browser was restarted out from under the agent"
    )


def test_version_never_starts_an_x_server(tmp_path: Path) -> None:
    """Asking which version is installed must not cost a browser."""
    environment = _workspace(tmp_path, config=False, display=False)
    result = _run(environment, "--version")

    assert not _bootstrapped(environment)
    assert result.returncode == 0


def test_start_browser_own_calls_do_not_recurse(tmp_path: Path) -> None:
    """`start-browser` runs `agent-browser` itself; without the guard that is a
    fork bomb rather than a bootstrap."""
    environment = _workspace(tmp_path, config=False, display=False)
    environment["LEMMA_BROWSER_BOOTSTRAP"] = "1"
    _run(environment, "open")

    assert not _bootstrapped(environment)


def test_the_guard_is_set_for_the_nested_calls(tmp_path: Path) -> None:
    """The recursion guard only works if the bootstrap actually exports it."""
    environment = _workspace(tmp_path, config=False, display=False)
    _run(environment, "open")

    assert Path(environment["_MARKER"]).read_text().strip() == "1"
