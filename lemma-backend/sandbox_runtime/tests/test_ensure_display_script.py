"""What `lemma-ensure-display` asks the browser to do, and when.

The script had no test at all, and the thing it got wrong was invisible from
Python: it ran `agent-browser open` with no URL. Measured on the real image
with agent-browser 0.37.1, a bare `open` relaunches Chrome onto a throwaway
`--user-data-dir=/tmp/agent-browser-chrome-<uuid>`, while `open <url>` keeps
it on the configured profile. Chrome writes `DevToolsActivePort` into
whichever directory it is actually using, so after a bare open the port file
in the durable profile names the previous launch and answers nothing --
which is what `chrome.live_port` reads, and through it `/health`'s `chrome`
field, `/vnc`'s refusal, `/targets` and both cookie routes.

End to end on the image: after `lemma-ensure-display about:blank`,
`live_port()` returned the live port; after a bare `lemma-ensure-display` on
top of that same healthy browser it raised. A viewer attaching was breaking
the thing that tells the backend a browser is there.

These tests drive the real script with stubs on `PATH`, in the manner of
`test_agent_browser_bootstrap.py`. They assert only the open decision -- the
display bring-up above it is stubbed out, because a unit test that started an
X server would be testing Xvfb.
"""

from __future__ import annotations

from pathlib import Path
import socket
import subprocess

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "sandbox-images/scripts/lemma-ensure-display.sh"
)


def _stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


def _workspace(
    tmp_path: Path, *, browser_live: bool, vnc_port: int
) -> tuple[dict[str, str], Path]:
    """A sandbox whose display is already up, so only the open decision runs.

    `pgrep` answers yes to everything, which is how each "is it already
    running" guard in the script short-circuits: this test is not about
    whether Xvfb starts.

    `vnc_port` names a socket the test really is listening on. The script
    waits on `/dev/tcp` for x11vnc before it sizes the display down, and that
    is the one wait a `PATH` stub cannot fake -- left unbound it costs about
    eight seconds a case, which is not a price the unit lane should pay for a
    test about one argument.
    """
    binaries = tmp_path / "bin"
    binaries.mkdir()
    opened = tmp_path / "opened"

    _stub(binaries, "pgrep", "exit 0")
    _stub(binaries, "pkill", "exit 0")
    _stub(binaries, "xrandr", 'echo "current 1440 x 960"')
    _stub(binaries, "start-browser-relay", "exit 0")
    _stub(binaries, "set-display-size", "exit 0")
    _stub(binaries, "matchbox-window-manager", "exit 0")
    _stub(binaries, "x11vnc", "exit 0")
    _stub(binaries, "websockify", "exit 0")
    _stub(binaries, "curl", "exit 0")
    _stub(binaries, "browser-is-live", "exit 0" if browser_live else "exit 1")
    _stub(binaries, "sha256sum", 'cat >/dev/null; echo "deadbeef  -"')
    # Records every argv it is given, one invocation per line.
    _stub(binaries, "agent-browser", f'echo "$*" >> "{opened}"')

    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "DevToolsActivePort").write_text("1234\n/devtools/browser/x")

    return (
        {
            "PATH": f"{binaries}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "DISPLAY": ":99",
            "AGENT_BROWSER_PROFILE": str(profile),
            "AGENT_BROWSER_CONFIG": str(tmp_path / "config.json"),
            "XDG_RUNTIME_DIR": str(tmp_path / "run"),
            "LEMMA_BROWSER_PROXY_FILE": str(tmp_path / "proxy-decision"),
            "LEMMA_BROWSER_VNC_PORT": str(vnc_port),
            "LEMMA_BROWSER_VNC_WS_PORT": str(vnc_port),
        },
        opened,
    )


@pytest.fixture
def vnc_port() -> int:
    """A bound port standing in for x11vnc's, so the script's wait returns."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    try:
        yield listener.getsockname()[1]
    finally:
        listener.close()


def _run(environment: dict[str, str], *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), *arguments],
        capture_output=True,
        text=True,
        env=environment,
        timeout=60,
    )


def _opens(record: Path) -> list[str]:
    if not record.exists():
        return []
    return [line for line in record.read_text().splitlines() if line.startswith("open")]


class TestTheScriptNeverOpensWithoutAUrl:
    """The whole finding, as three cases."""

    def test_a_cold_sandbox_opens_about_blank(
        self, tmp_path: Path, vnc_port: int
    ) -> None:
        """Not a bare `open`. A URL is what keeps Chrome on the configured
        profile, and therefore what keeps its recorded port answerable."""
        environment, opened = _workspace(
            tmp_path, browser_live=False, vnc_port=vnc_port
        )

        result = _run(environment)

        assert result.returncode == 0, result.stderr
        assert _opens(opened) == ["open about:blank"]

    def test_a_live_browser_is_left_alone(self, tmp_path: Path, vnc_port: int) -> None:
        """`open` is not a cheap no-op -- it relaunches. This script runs on
        every viewer attach, so opening anything here would throw away the
        page somebody is watching each time the pane reconnects."""
        environment, opened = _workspace(tmp_path, browser_live=True, vnc_port=vnc_port)

        result = _run(environment)

        assert result.returncode == 0, result.stderr
        assert _opens(opened) == []

    def test_a_caller_supplied_url_still_wins(
        self, tmp_path: Path, vnc_port: int
    ) -> None:
        """The sign-in path steers the browser at an origin, and that must
        reach `open` unchanged -- including over a browser already running,
        which is the case it exists for."""
        environment, opened = _workspace(tmp_path, browser_live=True, vnc_port=vnc_port)

        result = _run(environment, "https://example.com/login")

        assert result.returncode == 0, result.stderr
        assert _opens(opened) == ["open https://example.com/login"]


class TestTheProbeIsShared:
    """`browser-is-live` is one script because two callers ask the same
    question, and a copy of it in each is how they come to disagree."""

    def test_save_webpage_uses_the_shared_probe(self) -> None:
        capture = (SCRIPT.parent / "save-webpage.sh").read_text()
        assert "browser-is-live" in capture
        assert "DevToolsActivePort" not in capture, (
            "save-webpage should ask the shared probe rather than re-implement "
            "the port-file check it used to own"
        )

    def test_the_probe_refuses_when_nothing_answers(self, tmp_path: Path) -> None:
        """A port file Chrome left behind is not a running browser -- the
        case this probe exists for, and the one agent-browser now produces on
        every launch by recording the *previous* port."""
        probe = SCRIPT.parent / "browser-is-live.sh"
        profile = tmp_path / "profile"
        profile.mkdir()
        (profile / "DevToolsActivePort").write_text("9\n/devtools/browser/x")

        result = subprocess.run(
            ["/bin/bash", str(probe)],
            capture_output=True,
            env={"PATH": "/usr/bin:/bin", "AGENT_BROWSER_PROFILE": str(profile)},
            timeout=30,
        )

        assert result.returncode != 0

    def test_the_probe_refuses_when_there_is_no_port_file(self, tmp_path: Path) -> None:
        probe = SCRIPT.parent / "browser-is-live.sh"
        result = subprocess.run(
            ["/bin/bash", str(probe)],
            capture_output=True,
            env={"PATH": "/usr/bin:/bin", "AGENT_BROWSER_PROFILE": str(tmp_path)},
            timeout=30,
        )

        assert result.returncode != 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))


class TestAStaleDaemonIsRestartedOnce:
    """The failure that 407s every request through a healthy proxy.

    Installing the browser's network controls is also what answers Chrome's
    `Fetch.authRequired`. When `Fetch.enable` fails with "Session with given
    id not found", that answer never comes, so a perfectly good credentialed
    proxy returns 407 on everything and no page loads. Seen in production,
    where an agent spent twenty commands trying to reason its way out --
    nothing in the message says the daemon is the thing that is wrong.
    """

    def test_a_stale_session_is_retried_after_a_close(
        self, tmp_path: Path, vnc_port: int
    ) -> None:
        environment, opened = _workspace(
            tmp_path, browser_live=False, vnc_port=vnc_port
        )
        # Fails the way the daemon does, then succeeds once it is restarted.
        attempts = tmp_path / "attempts"
        _stub(
            tmp_path / "bin",
            "agent-browser",
            f'echo "$*" >> "{opened}"\n'
            f'case "$1" in\n'
            f"  open)\n"
            f'    n=$(wc -l < "{attempts}" 2>/dev/null || echo 0)\n'
            f'    echo x >> "{attempts}"\n'
            f'    if [ "$n" -eq 0 ]; then\n'
            f'      echo "Failed to install browser network controls: CDP error'
            f' (Fetch.enable): Session with given id not found." >&2\n'
            f"      exit 1\n"
            f"    fi\n"
            f"    exit 0 ;;\n"
            f"  *) exit 0 ;;\n"
            f"esac",
        )

        result = _run(environment)

        assert result.returncode == 0, result.stderr
        calls = (opened.read_text() if opened.exists() else "").splitlines()
        assert any(c.startswith("close --all") for c in calls), calls
        assert len([c for c in calls if c.startswith("open")]) == 2, calls

    def test_a_network_control_failure_that_is_not_stale_is_not_retried(
        self, tmp_path: Path, vnc_port: int
    ) -> None:
        """The near match, and why the condition needs both halves.

        An alternation on either fragment meant any failure that merely
        mentioned the network controls -- a permanent one included -- closed
        every session in the sandbox and retried. That costs a person
        whatever else they had open, for a fault a retry cannot fix.
        """
        environment, opened = _workspace(
            tmp_path, browser_live=False, vnc_port=vnc_port
        )
        _stub(
            tmp_path / "bin",
            "agent-browser",
            f'echo "$*" >> "{opened}"\n'
            'case "$1" in\n'
            "  open)\n"
            '    echo "Failed to install browser network controls: CDP error'
            ' (Fetch.enable): Target closed." >&2\n'
            "    exit 1 ;;\n"
            "  *) exit 0 ;;\n"
            "esac",
        )

        result = _run(environment)

        assert result.returncode != 0
        calls = (opened.read_text() if opened.exists() else "").splitlines()
        assert not any(c.startswith("close --all") for c in calls), calls
        assert len([c for c in calls if c.startswith("open")]) == 1, calls

    def test_a_failure_that_is_not_staleness_is_not_retried(
        self, tmp_path: Path, vnc_port: int
    ) -> None:
        """One retry, and only for this fault. Anything else that fails twice
        only delays the error reaching somebody."""
        environment, opened = _workspace(
            tmp_path, browser_live=False, vnc_port=vnc_port
        )
        _stub(
            tmp_path / "bin",
            "agent-browser",
            f'echo "$*" >> "{opened}"\n'
            'case "$1" in\n'
            '  open) echo "net::ERR_NAME_NOT_RESOLVED" >&2; exit 1 ;;\n'
            "  *) exit 0 ;;\n"
            "esac",
        )

        result = _run(environment)

        assert result.returncode != 0
        calls = (opened.read_text() if opened.exists() else "").splitlines()
        assert len([c for c in calls if c.startswith("open")]) == 1, calls
        assert not any(c.startswith("close --all") for c in calls), calls


class TestTheProxyIsTheServersDecision:
    """It could be given and never taken back.

    The proxy used to be baked into the sandbox's creation environment, so
    clearing the pool server-side left every existing sandbox proxied until
    it was replaced -- and workspace sandboxes are not replaced on drift.
    The server writes a one-line file now and this script reads it every
    run. Verified on the real image end to end: `p1`, then `p2`, then empty,
    produced exactly those three states on Chrome's command line.
    """

    def _config(self, environment: dict[str, str]) -> dict:
        import json

        return json.loads(Path(environment["AGENT_BROWSER_CONFIG"]).read_text())

    def test_a_decision_naming_a_proxy_reaches_the_config(
        self, tmp_path: Path, vnc_port: int
    ) -> None:
        environment, _ = _workspace(tmp_path, browser_live=False, vnc_port=vnc_port)
        Path(environment["LEMMA_BROWSER_PROXY_FILE"]).write_text(
            "http://user:pw@proxy.test:8080\n"
        )

        assert _run(environment).returncode == 0
        config = self._config(environment)
        assert config["proxy"] == "http://user:pw@proxy.test:8080"
        assert "disable_non_proxied_udp" in config["args"], (
            "without the WebRTC flag the sandbox's real IP leaks in ICE "
            "candidates gathered outside the proxy"
        )

    def test_an_empty_decision_withdraws_it(
        self, tmp_path: Path, vnc_port: int
    ) -> None:
        """The case that was impossible before. Empty is a decision, not an
        absence."""
        environment, _ = _workspace(tmp_path, browser_live=False, vnc_port=vnc_port)
        Path(environment["LEMMA_BROWSER_PROXY_FILE"]).write_text("")

        assert _run(environment).returncode == 0
        config = self._config(environment)
        assert "proxy" not in config
        assert "disable_non_proxied_udp" not in config["args"]

    def test_a_stale_baked_environment_variable_does_not_win(
        self, tmp_path: Path, vnc_port: int
    ) -> None:
        """Load-bearing, not tidiness. Measured on the image: with both set,
        the environment variable wins -- so a value baked into an older
        sandbox would silently override the server's current answer."""
        environment, _ = _workspace(tmp_path, browser_live=False, vnc_port=vnc_port)
        Path(environment["LEMMA_BROWSER_PROXY_FILE"]).write_text("")
        environment["AGENT_BROWSER_PROXY"] = "http://stale.test:1234"

        assert _run(environment).returncode == 0
        config = self._config(environment)
        assert "proxy" not in config
        assert "stale.test" not in Path(environment["AGENT_BROWSER_CONFIG"]).read_text()

    def test_a_decision_written_the_way_the_server_writes_it_reaches_the_config(
        self, tmp_path: Path, vnc_port: int
    ) -> None:
        """No trailing newline -- and this is not a nicety, it is the bug.

        `browser_proxy.decision_bytes` returns the bare URL, unterminated,
        and that is what `write_file` puts in the sandbox. `read` returns
        non-zero when it reaches end-of-file without a newline, *after*
        assigning the line, and the script cleared the variable on that
        branch. So the branch was taken on every proxy the server ever
        delivered: measured on the image, a decision file holding a real URL
        produced a `config.json` with no `proxy` key and a stamp of the
        empty string. The mechanism was inert in production.

        Every other case in this class writes `"...\n"`, which is why none
        of them caught it -- the test wrote the file in a shape the thing
        that really writes it never produces.
        """
        environment, _ = _workspace(tmp_path, browser_live=False, vnc_port=vnc_port)
        Path(environment["LEMMA_BROWSER_PROXY_FILE"]).write_bytes(
            b"http://user:pw@proxy.test:8080"
        )

        assert _run(environment).returncode == 0
        assert self._config(environment)["proxy"] == "http://user:pw@proxy.test:8080"

    def test_the_config_is_not_world_readable_when_it_holds_a_credential(
        self, tmp_path: Path, vnc_port: int
    ) -> None:
        environment, _ = _workspace(tmp_path, browser_live=False, vnc_port=vnc_port)
        Path(environment["LEMMA_BROWSER_PROXY_FILE"]).write_text(
            "http://user:pw@proxy.test:8080\n"
        )

        _run(environment)

        mode = Path(environment["AGENT_BROWSER_CONFIG"]).stat().st_mode & 0o777
        assert mode == 0o600, f"config.json is {oct(mode)}, and it holds a password"


class TestLoopbackFallsThroughToTheHost:
    """`localhost` in the sandbox, and the machine the person is sitting at.

    On Desktop the owner's agent can run commands on their Mac, so `npm run
    dev` listens on *their* machine while this browser is in a container.
    Verified end to end on the real image with a real Chromium: with a server
    on the host and nothing on that port in the sandbox, `localhost:<port>`
    reached the host; with the sandbox serving the same port, both spellings
    stayed in the sandbox.

    Whether there is a host to fall through to at all is whether the loopback
    relay's socket is there: guestd mounts it into the installation owner's
    own workspace and nowhere else.
    """

    def _config(self, environment: dict[str, str]) -> dict:
        import json

        return json.loads(Path(environment["AGENT_BROWSER_CONFIG"]).read_text())

    @pytest.fixture
    def relay_socket(self):
        """A real listening Unix socket where guestd would mount the relay's.

        Under /tmp: a socket path is limited to ~104 bytes on macOS, and
        pytest's temporary directories are longer than that there.
        """
        import shutil
        import tempfile

        directory = Path(tempfile.mkdtemp(prefix="lemma-relay-", dir="/tmp"))
        path = directory / "relay.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        listener.listen(1)
        try:
            yield path
        finally:
            listener.close()
            shutil.rmtree(directory, ignore_errors=True)

    def _sandbox(self, tmp_path: Path, vnc_port: int, *, relay: Path | None):
        environment, _ = _workspace(tmp_path, browser_live=True, vnc_port=vnc_port)
        environment["LEMMA_HOST_LOOPBACK_SOCKET"] = str(
            relay if relay is not None else tmp_path / "no-relay" / "relay.sock"
        )
        # The fall-through itself is its own module with its own tests; what is
        # under test here is whether this script starts one and tells Chrome.
        _stub(tmp_path / "bin", "python3", "sleep 300")
        return environment

    def test_a_sandbox_without_the_relay_is_left_alone(
        self, tmp_path, vnc_port
    ) -> None:
        """Invited people's sandboxes, Docker, E2B and Windows have no relay.

        The flag is not harmless there: it would point Chrome at a proxy with
        nowhere to send what it cannot serve, and Chrome fails a navigation
        outright when its proxy refuses.
        """
        environment = self._sandbox(tmp_path, vnc_port, relay=None)
        _run(environment)
        arguments = self._config(environment)["args"]
        assert "--proxy-server" not in arguments
        assert "loopback" not in arguments

    def test_the_owners_sandbox_points_chrome_at_the_fall_through(
        self, tmp_path, vnc_port, relay_socket
    ) -> None:
        """With the relay mounted, Chrome is sent loopback through the proxy.

        The proxy is already listening here, as it is on every launch after
        the first, so the script's own start is not what is under test.
        """
        environment = self._sandbox(tmp_path, vnc_port, relay=relay_socket)
        with socket.socket() as proxy:
            proxy.bind(("127.0.0.1", 0))
            proxy.listen(8)
            port = proxy.getsockname()[1]
            environment["LEMMA_HOST_FALLBACK_PORT"] = str(port)
            _run(environment)
        arguments = self._config(environment)["args"]
        assert f"--proxy-server=http://127.0.0.1:{port}" in arguments
        assert "--proxy-bypass-list=<-loopback>" in arguments

    def test_a_server_assigned_proxy_wins_and_the_fall_through_stands_down(
        self, tmp_path, vnc_port, relay_socket
    ) -> None:
        """Chrome takes one `--proxy-server`, so the two cannot both be on.

        A sandbox being proxied for sign-in reasons is not one somebody is
        pointing at their own dev server, so the residential proxy keeps the
        flag and the fall-through does not run.
        """
        environment = self._sandbox(tmp_path, vnc_port, relay=relay_socket)
        Path(environment["LEMMA_BROWSER_PROXY_FILE"]).parent.mkdir(
            parents=True, exist_ok=True
        )
        Path(environment["LEMMA_BROWSER_PROXY_FILE"]).write_text(
            "http://residential.invalid:9091"
        )
        _run(environment)
        config = self._config(environment)
        assert config["proxy"] == "http://residential.invalid:9091"
        assert "127.0.0.1:4851" not in config["args"]
        assert "loopback" not in config["args"]
