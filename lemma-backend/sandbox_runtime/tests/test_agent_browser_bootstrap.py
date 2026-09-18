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

from contextlib import suppress
import os
from pathlib import Path
import socket
import subprocess
import time

import pytest

# Bound display sockets, closed and unlinked after each test so a second run
# does not find the first one's address already taken.
_SOCKETS: list[socket.socket] = []

# Stand-ins for a running X server. Stopped after each test, or a stray one
# would make the next test think a display it never started is up.
_SERVERS: list[subprocess.Popen] = []


@pytest.fixture(autouse=True)
def _release_display_sockets():
    yield
    while _SERVERS:
        server = _SERVERS.pop()
        server.terminate()
        with suppress(subprocess.TimeoutExpired):
            server.wait(timeout=5)
    while _SOCKETS:
        listener = _SOCKETS.pop()
        address = listener.getsockname()
        listener.close()
        Path(address).unlink(missing_ok=True)


def _serving(display_number: int) -> bool:
    """Whether `pgrep` can see a stand-in for this display, as the wrapper does."""
    found = subprocess.run(
        ["pgrep", "-f", f"Xvfb :{display_number} "], capture_output=True
    )
    return found.returncode == 0


SCRIPT = Path(__file__).resolve().parents[2] / "sandbox-images/scripts/lemma-node-tool"


def _workspace(
    tmp_path: Path, *, config: bool, display: bool, stale_socket: bool = False
) -> dict[str, str]:
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

    # A display is up when an X server is *running* on it. It used to be
    # modelled here as "a socket exists at the path X clients look for", which
    # is what the wrapper used to test -- and the two come apart exactly when it
    # matters: the socket is a file in the container's writable layer and
    # survives a restart, while the process does not. `stale_socket` is that
    # state, and it is the one that broke every browser in a resumed sandbox.
    #
    # An unusual display number keeps this away from any real X server on the
    # machine running the tests.
    display_number = 77 if (display or stale_socket) else 78
    if display or stale_socket:
        os.makedirs("/tmp/.X11-unix", exist_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(f"/tmp/.X11-unix/X{display_number}")
        _SOCKETS.append(listener)
    if display:
        # A stand-in whose command line is what `pgrep -f` looks for. It must
        # not `exec`, or the argv the wrapper matches on is replaced by the
        # sleep's own.
        fake_server = binaries / "Xvfb"
        fake_server.write_text("#!/bin/sh\nsleep 30\n")
        fake_server.chmod(0o755)
        _SERVERS.append(
            subprocess.Popen(
                [str(fake_server), f":{display_number}", "-screen", "0", "1x1x24"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
        # Waited for: `Popen` returns before the shell is on the process table,
        # and a test that raced it would assert "already up" against nothing.
        deadline = time.monotonic() + 5
        while not _serving(display_number) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert _serving(display_number), "the stand-in X server never appeared"

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


def test_a_socket_left_by_a_dead_x_server_does_not_count_as_a_display(
    tmp_path: Path,
) -> None:
    """The state a resumed sandbox is in, and the one this guard used to miss.

    `/tmp/.X11-unix/X99` is an ordinary file in the container's writable layer.
    It survives whatever killed Xvfb -- an ungraceful stop, a Docker daemon
    restart, a host reboot, a quiesce that could not reach the runtime, or E2B,
    which pauses without quiescing and keeps `/tmp`. The process does not.

    Reading that file as "a display is up" meant this returned early and
    `start-browser` -- the one thing that would have started Xvfb -- was never
    called, so every browser command in the sandbox failed with "Missing X
    server or $DISPLAY" for the life of the container, with nothing inside able
    to clear it. The test above passed throughout, because it built its "already
    up" display out of the same stale file.
    """
    environment = _workspace(tmp_path, config=True, display=False, stale_socket=True)

    _run(environment, "snapshot", "-i")

    assert _bootstrapped(environment), (
        "a socket with no X server behind it was taken for a running display"
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


DOCKERFILE = Path(__file__).resolve().parents[2] / "sandbox-images/Dockerfile.workspace"


def test_the_wrapper_is_found_before_the_raw_package_binary() -> None:
    """Every test above drives the wrapper directly. The image has to reach it.

    `/usr/local/bin` is where the `lemma-node-tool` wrappers are symlinked, and
    `/opt/lemma-node/node_modules/.bin` is where npm puts the package's own
    shim. With the npm directory first, a bare `agent-browser` -- what an agent
    types, and what the skill's own examples show -- resolved to the shim, which
    cannot bootstrap. The wrapper existed, was correct, was tested, and was
    never reached: the two errors it was written to prevent both turned up in a
    real transcript.

    Asserted against the image rather than against a stub, because the ordering
    is the part no wrapper test can see.
    """
    path_line = next(
        line
        for line in DOCKERFILE.read_text().splitlines()
        if line.strip().startswith("PATH=")
    )
    entries = path_line.strip().removeprefix("PATH=").rstrip("\\").strip().split(":")

    assert "/usr/local/bin" in entries, path_line
    assert "/opt/lemma-node/node_modules/.bin" in entries, path_line
    assert entries.index("/usr/local/bin") < entries.index(
        "/opt/lemma-node/node_modules/.bin"
    ), "the npm shim would win over the wrapper"


# ---------------------------------------------------------------------------
# Not typing over somebody who has the wheel
# ---------------------------------------------------------------------------


def _holding_the_wheel(
    tmp_path: Path, environment: dict[str, str], session: str
) -> None:
    """Write the lease the relay writes while a person is driving."""
    import hashlib

    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir(exist_ok=True)
    digest = hashlib.sha256(session.encode()).hexdigest()[:32]
    (wheel_dir / digest).write_text("a-viewers-token")
    environment["LEMMA_WHEEL_DIR"] = str(wheel_dir)


def test_a_command_runs_even_while_somebody_is_watching(tmp_path: Path) -> None:
    """There is no driving lease any more, and this is what took its place.

    The lease refused `agent-browser` while a person held the relay's control
    socket. It went because it never covered the case it was written for and
    only ever cost the case it did reach: a sign-in runs in `login-<host>`, a
    session the agent never touches, so the lease was a no-op at the one
    moment somebody was typing a password -- while an ordinary watch attaches
    to the agent's *own* session, so the only thing it ever stopped was the
    agent using its own browser while somebody looked at it.

    A stale lease file left by an older relay must not resurrect that: the
    wrapper does not read one.
    """
    environment = _workspace(tmp_path, config=True, display=True)
    environment["AGENT_BROWSER_SESSION"] = "login-example.com"
    _holding_the_wheel(tmp_path, environment, "login-example.com")

    result = _run(environment, "click", "@e3")

    assert result.returncode == 0, result
    assert "click @e3" in result.stdout


def test_version_needs_no_browser(tmp_path: Path) -> None:
    """Asking which version is installed touches no page, so it must not be
    what starts Xvfb and a browser."""
    environment = _workspace(tmp_path, config=True, display=True)
    environment["AGENT_BROWSER_SESSION"] = "login-example.com"
    _holding_the_wheel(tmp_path, environment, "login-example.com")

    result = _run(environment, "--version")

    assert result.returncode == 0, result
