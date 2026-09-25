"""Loopback in the sandbox, and the machine behind it.

The two machines here are IPv4 and IPv6 loopback. The fall-through probes the
sandbox at `127.0.0.1`; the Mac is reached through the loopback relay, which
these tests stand in for with a Unix socket that speaks its protocol and
connects to `::1` -- so "the sandbox" and "the Mac" are two genuinely
different addresses that share a port number, which is the whole situation
being tested. Both are up by default on Linux and macOS.
"""

from __future__ import annotations

import http.server
import shutil
import socket
import socketserver
import tempfile
import threading
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from sandbox_runtime import host_fallback
from sandbox_runtime.host_fallback import open_relay, open_upstream, serve


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


def _running(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _stop(*servers) -> None:
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class FakeRelay:
    """The loopback relay as the sandbox sees it: guestd's socket, the Mac
    behind it. Refuses the ports in `refused`, connects to `::1` otherwise,
    and records every port it was asked for."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.asked: list[int] = []
        self.refused: set[int] = set()
        self.greeting = b""
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(str(path))
        self._listener.listen(16)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        line = b""
        while not line.endswith(b"\n"):
            chunk = client.recv(1)
            if not chunk:
                client.close()
                return
            line += chunk
        port = int(line)
        self.asked.append(port)
        if port in self.refused:
            client.sendall(b"error that port is one of Lemma's own\n")
            client.close()
            return
        try:
            upstream = socket.create_connection(("::1", port), timeout=2)
        except OSError:
            client.sendall(b"error nothing on this Mac is listening on that port\n")
            client.close()
            return
        client.sendall(b"ok\n" + self.greeting)
        try:
            host_fallback._splice(client, upstream)
        finally:
            client.close()
            upstream.close()

    def close(self) -> None:
        self._listener.close()


@pytest.fixture
def relay_dir() -> Iterator[Path]:
    # Under /tmp: a Unix socket path is limited to ~104 bytes on macOS, and
    # pytest's own temporary directories are longer than that there.
    directory = Path(tempfile.mkdtemp(prefix="lemma-relay-", dir="/tmp"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def relay(relay_dir: Path, monkeypatch) -> Iterator[FakeRelay]:
    """The owner's sandbox: the relay's socket is mounted."""
    path = relay_dir / "relay.sock"
    monkeypatch.setenv("LEMMA_HOST_LOOPBACK_SOCKET", str(path))
    fake = FakeRelay(path)
    yield fake
    fake.close()


@pytest.fixture
def no_relay(relay_dir: Path, monkeypatch) -> None:
    """Every other sandbox: nothing is mounted where the socket would be."""
    monkeypatch.setenv("LEMMA_HOST_LOOPBACK_SOCKET", str(relay_dir / "relay.sock"))


def test_a_port_the_sandbox_serves_stays_the_sandboxs(free_port, relay) -> None:
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
        assert relay.asked == [], "the relay was asked for a port the sandbox serves"
    finally:
        _stop(sandbox, host)


def test_a_port_only_the_mac_serves_goes_through_the_relay(free_port, relay) -> None:
    """The half this exists for: `npm run dev` on the person's own Mac."""
    host = _running(_Server6(("::1", free_port), _say("the host")))
    try:
        connection, where = open_upstream("localhost", free_port)
        assert where == "host"
        assert relay.asked == [free_port]
        connection.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        # The server writes its headers and its body separately, so one
        # `recv` may return only the headers; read until the body is in or
        # the server stops sending.
        connection.settimeout(10)
        response = b""
        while not response.endswith(b"the host"):
            chunk = connection.recv(4096)
            if not chunk:
                break
            response += chunk
        assert response.endswith(b"the host"), response
        connection.close()
    finally:
        _stop(host)


def test_a_sandbox_without_the_relay_never_leaves_itself(free_port, no_relay) -> None:
    """Invited people's sandboxes, E2B, Docker: no socket, no fall-through.

    Even with a server on the other address, the answer is "nothing here"
    -- which is what it was before the fall-through existed.
    """
    host = _running(_Server6(("::1", free_port), _say("the host")))
    try:
        connection, where = open_upstream("localhost", free_port)
        assert (connection, where) == (None, "none")
    finally:
        _stop(host)


def test_a_port_the_relay_refuses_is_refused(free_port, relay) -> None:
    """The Mac's end refuses Lemma's own ports; the sandbox gets nothing."""
    host = _running(_Server6(("::1", free_port), _say("the backend")))
    relay.refused.add(free_port)
    try:
        connection, where = open_upstream("127.0.0.1", free_port)
        assert (connection, where) == (None, "none")
        assert relay.asked == [free_port]
    finally:
        _stop(host)


def test_a_port_nobody_serves_is_refused_rather_than_hung(free_port, relay) -> None:
    connection, where = open_upstream("localhost", free_port)
    assert connection is None
    assert where == "none"


def test_a_real_hostname_is_never_sent_to_the_relay(relay) -> None:
    """Only loopback falls through. Everything else is the internet."""
    connection, where = open_upstream("example.invalid", 80)
    assert (connection, where) == (None, "none")
    assert relay.asked == []


def test_bytes_behind_the_relays_answer_are_kept(free_port, relay) -> None:
    """A server that speaks first must not lose its first bytes to the
    reading of the relay's `ok`."""
    host = _running(_Server6(("::1", free_port), _say("the host")))
    relay.greeting = b"hello"
    try:
        connection = open_relay(free_port)
        assert connection is not None
        assert connection.recv(5) == b"hello"
        connection.close()
    finally:
        _stop(host)


def test_the_proxy_serves_a_fall_through_over_http(free_port, relay) -> None:
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
        _stop(host)
