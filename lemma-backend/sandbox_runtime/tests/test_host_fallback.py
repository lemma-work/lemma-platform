"""Loopback in the sandbox, and the machine behind it.

The two machines here are IPv4 and IPv6 loopback. The fall-through probes the
sandbox at `127.0.0.1` and the host at whatever `LEMMA_HOST_ALIAS` names, so
pointing the alias at `::1` gives two genuinely different addresses that share
a port number -- which is the whole situation being tested, and which a single
loopback address cannot express. Both are up by default on Linux and macOS.
"""

from __future__ import annotations

import http.server
import socket
import socketserver
import threading
import urllib.request

import pytest

from sandbox_runtime.host_fallback import open_upstream, serve


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _Server6(_Server):
    address_family = socket.AF_INET6


def _say(what: str):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            body = what.encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            return

    return Handler


@pytest.fixture
def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def host_alias(monkeypatch):
    """The other machine, as this sandbox would name it."""
    monkeypatch.setenv("LEMMA_HOST_ALIAS", "::1")


def _running(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_a_port_the_sandbox_serves_stays_the_sandboxs(free_port, host_alias) -> None:
    """The half that must not regress.

    The browser skill tells an agent to reach a site it built at
    `127.0.0.1:<port>` and the apps reference at `localhost:<port>`. Sending
    either of those to the host would break previewing your own work, which is
    why this is a fall-through and not a redirect.
    """
    sandbox = _running(_Server(("127.0.0.1", free_port), _say("the sandbox")))
    host = _running(_Server6(("::1", free_port), _say("the host")))
    try:
        for spelling in ("localhost", "127.0.0.1"):
            connection, where = open_upstream(spelling, free_port)
            assert where == "sandbox", f"{spelling} left the sandbox"
            connection.close()
    finally:
        sandbox.shutdown()
        host.shutdown()


def test_a_port_only_the_host_serves_reaches_the_host(free_port, host_alias) -> None:
    """The half this exists for: `npm run dev` on the person's own machine."""
    host = _running(_Server6(("::1", free_port), _say("the host")))
    try:
        connection, where = open_upstream("localhost", free_port)
        assert where == "host"
        connection.close()
    finally:
        host.shutdown()


def test_a_port_nobody_serves_is_refused_rather_than_hung(
    free_port, host_alias
) -> None:
    connection, where = open_upstream("localhost", free_port)
    assert connection is None
    assert where == "none"


def test_a_real_hostname_is_never_sent_to_the_host(free_port, host_alias) -> None:
    """Only loopback falls through. Everything else is the internet."""
    connection, where = open_upstream("example.invalid", 80)
    assert where == "none"
    assert connection is None


def test_the_proxy_serves_a_fall_through_over_http(free_port, host_alias) -> None:
    """End to end, through the proxy a browser would actually be pointed at.

    `--proxy-server` plus `--proxy-bypass-list=<-loopback>` is what makes
    Chrome send loopback here at all; measured on the workspace image, without
    the bypass override Chrome answers `localhost` itself and the proxy sees
    nothing.
    """
    host = _running(_Server6(("::1", free_port), _say("the host")))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        proxy_port = probe.getsockname()[1]
    ready = threading.Event()
    threading.Thread(
        target=serve, args=(proxy_port,), kwargs={"ready": ready}, daemon=True
    ).start()
    assert ready.wait(5), "the fall-through proxy did not start"
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{proxy_port}"})
        )
        with opener.open(f"http://localhost:{free_port}/", timeout=10) as answer:
            assert answer.read() == b"the host"
    finally:
        host.shutdown()
