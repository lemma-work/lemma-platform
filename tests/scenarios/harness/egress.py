"""What Lemma says to the outside world, recorded once and replayed after.

The suite used to stand in for other people's servers by running small HTTP
servers on `127.0.0.1` and pointing Lemma at them. That worked, and it was the
wrong shape for this suite: a deployment cannot reach a server on the machine
running the tests, it needed an SSRF exemption production does not have, and
nobody points their Telegram surface at a bot API on their laptop. Worst of all
a hand-written stand-in cannot tell you it has drifted — when Telegram changes a
response, our imitation keeps returning the old one and the suite stays green.

So the stand-in is *derived from the real thing* instead. Everything Lemma sends
outward goes through one proxy:

    record   real credentials  ->  proxy  ->  real Telegram / Google / GitHub
    replay   no credentials    ->  proxy  ->  what was recorded, and nothing else

The recording is a committed artifact, and a diff in one is a third party
changing its API — which is precisely the signal the old fakes could never give.

Two properties are worth stating because they are what make this safe:

**A request nobody recorded is killed, not forwarded.** `server_replay_extra` is
set to `kill`, so a replay run cannot quietly reach the real internet.

**The backend is given the proxy's certificate authority and no other.** Any TLS
connection that does not go through the proxy therefore fails, loudly, rather
than silently succeeding against a real provider. A bypass is a broken test, not
an invisible one.

This is also the *observation* point. "What did Lemma send to Telegram?" used to
be three different recorder objects with three different shapes; it is one query
here, whatever the platform.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

#: Where recordings live. Committed, and reviewed like code.
CASSETTES = Path(__file__).resolve().parent.parent / "cassettes"

#: `record` drives real providers and writes what happened. `replay` serves what
#: was written and refuses anything else. `off` is for the scenarios that never
#: talk to a third party — most of them — so they pay nothing for this.
MODE_SETTING = "SCENARIOS_EGRESS"

HOW_LONG_TO_BOOT = 30.0


class ProxyUnavailable(AssertionError):
    """The proxy could not be started, or did not come up."""


def wanted_mode() -> str:
    """Which lane this run is: `fake` unless told otherwise.

    `fake` is the default because the fast lane needs somebody to answer for
    Telegram and for the connector provider, and the proxy is now who does.
    Before, the suite ran servers on loopback and pointed the product at them —
    which only worked with the SSRF guard turned off for every scenario.

    `off` stays available and means exactly what it says: no proxy, so the
    scenarios that need a third party have nothing to talk to.
    """
    mode = os.getenv(MODE_SETTING, "fake").strip().lower()
    if mode not in {"off", "fake", "record", "replay"}:
        raise ProxyUnavailable(
            f"{MODE_SETTING}={mode!r} is not one of off, fake, record, replay"
        )
    return mode


@dataclass
class Call:
    """One thing Lemma sent out, and what came back."""

    host: str
    method: str
    path: str
    url: str
    request_headers: dict[str, str]
    request_body: str
    status: int | None
    response_body: str
    failed: str | None

    @property
    def authorization(self) -> str:
        """What credential went out, if any. Lowercased key — see the addon."""
        return self.request_headers.get("authorization", "")

    def json_body(self) -> Any:
        try:
            return json.loads(self.request_body)
        except ValueError:
            return None


@dataclass
class Egress:
    """The proxy, for the length of a session."""

    mode: str
    proxy_url: str
    ca_bundle: str
    _control: str = ""
    _process: subprocess.Popen | None = None
    _cassette: Path | None = None
    _confdir: Path | None = None

    # --- what a scenario asks ------------------------------------------------

    def calls(self) -> list[Call]:
        """Everything Lemma has sent out since the last `forget()`."""
        if self.mode == "off":
            raise ProxyUnavailable(
                "nothing is recording. A scenario asserting on what Lemma sent "
                f"outward needs {MODE_SETTING}=record or replay, and should say "
                f"so with needs(EGRESS_RECORDED)"
            )
        answered = httpx.get(f"{self._control}/calls", timeout=10.0)
        answered.raise_for_status()
        return [Call(**row) for row in answered.json()]

    def calls_to(self, host: str, *, path_contains: str = "") -> list[Call]:
        """What Lemma sent to one third party, in order.

        `host` is matched as a suffix so `telegram.org` finds
        `api.telegram.org` without a scenario having to know which subdomain a
        client happened to use.
        """
        return [
            call
            for call in self.calls()
            if call.host.endswith(host) and path_contains in call.path
        ]

    def forget(self) -> None:
        """Start the scenario with a clean sheet. One run's traffic is not another's."""
        if self.mode == "off":
            return
        httpx.post(f"{self._control}/reset", timeout=10.0).raise_for_status()

    # --- what the stack is told ---------------------------------------------

    def environment(self) -> dict[str, str]:
        """What to boot the product with so its egress comes through here.

        No product change is involved: every outbound client in the backend is
        `httpx` with `trust_env` left on, and `slack_sdk` loads the same
        variables of its own accord (`base_client.py` calls
        `load_http_proxy_from_env`). The certificate bundle is the proxy's own
        and nothing else, which is what turns a bypass into a failure.
        """
        if self.mode == "off":
            return {}
        return {
            "HTTP_PROXY": self.proxy_url,
            "HTTPS_PROXY": self.proxy_url,
            "http_proxy": self.proxy_url,
            "https_proxy": self.proxy_url,
            # Postgres, Redis and SuperTokens are reached by name on the local
            # network and speak no TLS here; everything else is a third party
            # and belongs in the recording.
            "NO_PROXY": "localhost,127.0.0.1,::1",
            "no_proxy": "localhost,127.0.0.1,::1",
            "SSL_CERT_FILE": self.ca_bundle,
            "REQUESTS_CA_BUNDLE": self.ca_bundle,
        }


def _free_port() -> int:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        return int(taken.getsockname()[1])


def _mitmdump() -> list[str]:
    """How to run mitmproxy, preferring one already installed.

    A tool rather than a dependency, exactly like docker: the suite's own
    `pyproject.toml` stays thin, and nothing here imports mitmproxy.
    """
    found = shutil.which("mitmdump")
    if found:
        return [found]
    if shutil.which("uvx"):
        return ["uvx", "--from", "mitmproxy", "mitmdump"]
    raise ProxyUnavailable(
        "mitmproxy is not installed and `uvx` is not on PATH. Install it with "
        "`uv tool install mitmproxy`, or run with SCENARIOS_EGRESS=off for the "
        "scenarios that never talk to a third party."
    )


def start(mode: str, *, cassette: str, scratch: Path) -> Egress:
    """Bring the proxy up, or fail saying why.

    `cassette` names the recording: one per journey, so a change in what
    Telegram says shows up in a small diff rather than a large one.
    """
    if mode == "off":
        return Egress(mode="off", proxy_url="", ca_bundle="")

    CASSETTES.mkdir(parents=True, exist_ok=True)
    recording = CASSETTES / f"{cassette}.flows"
    if mode == "replay" and not recording.exists():
        raise ProxyUnavailable(
            f"there is no recording at {recording}. Make one with "
            f"`SCENARIOS_EGRESS=record`, against the real providers, and commit "
            f"it — a replay lane cannot invent what a third party would have said."
        )

    confdir = scratch / "mitmproxy"
    confdir.mkdir(parents=True, exist_ok=True)
    port, control = _free_port(), _free_port()

    settings = [
        "--set",
        f"confdir={confdir}",
        "--set",
        f"control_port={control}",
        # Lazily, or mitmproxy opens the upstream connection before the request
        # hook runs — to copy the real server's TLS certificate — and a host
        # that does not resolve fails there, before anything can redirect it.
        "--set",
        "connection_strategy=lazy",
        # Bodies are deliberately *not* streamed. mitmproxy streams a response
        # straight through without keeping it, so a recording made with
        # streaming on replays as "200 OK (content missing)" — which is a
        # response, and passes a status assertion, and contains nothing. That
        # cost an afternoon; leaving the default alone is the fix.
    ]
    if mode == "fake":
        settings += ["--set", "serve_fakes=true"]
    elif mode == "record":
        settings += ["-w", str(recording)]
    elif mode == "replay":
        settings += [
            "--set",
            f"server_replay={recording}",
            # The whole safety story in one setting: a request nobody recorded
            # is killed rather than forwarded, so a replay run cannot quietly
            # reach the real internet and pass for the wrong reason.
            "--set",
            "server_replay_extra=kill",
            # A scenario may poll — asking twice must not exhaust the recording.
            "--set",
            "server_replay_reuse=true",
            "--set",
            "server_replay_refresh=true",
        ]

    log = scratch / "egress.log"
    process = subprocess.Popen(  # noqa: S603 — arguments are ours, not a scenario's
        [
            *_mitmdump(),
            "--listen-host",
            "127.0.0.1",
            "-p",
            str(port),
            "-s",
            str(Path(__file__).resolve().parent / "egress_addon.py"),
            *settings,
        ],
        stdout=log.open("w"),
        stderr=subprocess.STDOUT,
    )

    egress = Egress(
        mode=mode,
        proxy_url=f"http://127.0.0.1:{port}",
        ca_bundle=str(confdir / "mitmproxy-ca-cert.pem"),
        _control=f"http://127.0.0.1:{control}",
        _process=process,
        _cassette=recording,
        _confdir=confdir,
    )
    _wait_for(egress, log)
    return egress


def _wait_for(egress: Egress, log: Path) -> None:
    """Wait on the control port, not the clock.

    The certificate authority is generated on first start, so the first run on a
    machine is slower than the rest — waiting a fixed time would be too short
    there and wasted everywhere else.
    """
    deadline = time.monotonic() + HOW_LONG_TO_BOOT
    while time.monotonic() < deadline:
        if egress._process is not None and egress._process.poll() is not None:
            raise ProxyUnavailable(
                f"the proxy exited immediately ({egress._process.returncode}). "
                f"Its log:\n{log.read_text()[-2000:]}"
            )
        try:
            httpx.get(f"{egress._control}/calls", timeout=1.0).raise_for_status()
        except (httpx.HTTPError, OSError):
            continue
        if Path(egress.ca_bundle).exists():
            return
    raise ProxyUnavailable(
        f"the proxy did not come up within {HOW_LONG_TO_BOOT:.0f}s. Its log:\n"
        f"{log.read_text()[-2000:] if log.exists() else '(no log)'}"
    )


def stop(egress: Egress) -> None:
    if egress._process is None:
        return
    egress._process.terminate()
    try:
        # Recording is only durable once mitmproxy has flushed and exited, so a
        # run that killed it outright would lose the last few calls it made.
        egress._process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        egress._process.kill()
        egress._process.wait(timeout=5)
