"""Loopback in the sandbox, falling through to the machine Lemma runs on.

On Desktop the owner's agent can run commands on their own Mac (host
execution), so `npm run dev` listens on *their* machine's loopback. The
agent's browser lives in a sandbox in the guest, where `localhost` is the
container. The two are presented as one machine and were not one, so a browser
asked for `http://localhost:3000` got a connection refused for a server that
was running the whole time.

This is the thing that makes the two agree, per request rather than per port:

* a loopback address the sandbox is serving stays the sandbox's, so an agent
  previewing a site it just built is unaffected -- which the browser skill
  tells it to reach at `127.0.0.1` and the apps reference at `localhost`, so
  neither spelling can be quietly reassigned;
* a loopback address nothing in the sandbox is serving is asked for again
  through the loopback relay, which reaches the same port on the Mac's own
  `127.0.0.1`.

"Nothing is serving it" is answered by connecting, not by reading a table:
a listener that came up a moment ago is a listener, and the alternative is a
cache that is wrong exactly when somebody has just started their dev server.

**The relay.** A Unix socket guestd mounts into one sandbox only -- the
installation owner's own workspace -- at `LEMMA_HOST_LOOPBACK_SOCKET`. Ask it
for a port (`3000\n`); it answers `ok\n` and then carries the bytes to the
Mac, or `error <reason>\n` and closes. The Mac's end refuses Lemma's own ports
and privileged ones. Every other sandbox has no socket, so there nothing falls
through and a port the sandbox is not serving is simply refused. See
docs/architecture/desktop-security.md, "The loopback relay".

This used to dial the host alias instead, over the guest's NAT. That reached
only servers listening on every interface -- not a dev server on `127.0.0.1`
-- and it was open to every sandbox, including invited people's.

Stdlib only and no imports from the rest of `sandbox_runtime`, because this
ships into the E2B template as well, where `test_e2b_templates_ship_their_imports`
enforces that whatever a shipped module imports ships too.
"""

from __future__ import annotations

import errno
import os
import selectors
import socket
import socketserver
import sys
import threading
from urllib.parse import urlsplit

#: The loopback names a request may carry. `[::1]` is how a bracketed IPv6
#: literal arrives in a request line; `::1` is how it arrives from `CONNECT`.
LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})

#: How long to wait for the sandbox's own loopback before deciding nothing is
#: there. Loopback either answers or refuses at once; this only bounds a
#: pathological case, and every millisecond of it is added to a request that is
#: going to the host anyway.
LOCAL_CONNECT_TIMEOUT = 0.25

#: How long to wait for a direct connection, or for the relay's answer. The
#: relay's answer includes the Mac connecting to the person's dev server,
#: which may be starting up.
HOST_CONNECT_TIMEOUT = 5.0

#: Where guestd mounts the loopback relay's socket, in the one sandbox that
#: has it.
DEFAULT_RELAY_SOCKET = "/run/lemma-host-loopback/relay.sock"

#: The longest answer line the relay sends: `error ` and a short sentence.
_MAX_ANSWER_BYTES = 256

_CHUNK = 65536


def relay_socket_path() -> str:
    return os.environ.get("LEMMA_HOST_LOOPBACK_SOCKET", DEFAULT_RELAY_SOCKET)


def _read_answer(connection: socket.socket) -> bytes | None:
    """The relay's one-line answer, without consuming a byte past it.

    A byte at a time because whatever follows the newline is the server's,
    and a server may speak first.
    """
    answer = b""
    while len(answer) < _MAX_ANSWER_BYTES:
        byte = connection.recv(1)
        if not byte:
            return None
        if byte == b"\n":
            return answer
        answer += byte
    return None


def open_relay(port: int) -> socket.socket | None:
    """A stream to `port` on the Mac's loopback, or None.

    None when this sandbox has no relay (every sandbox but the owner's), when
    the relay refused the port, or when nothing on the Mac is listening.
    """
    path = relay_socket_path()
    if not os.path.exists(path):
        return None
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(HOST_CONNECT_TIMEOUT)
        connection.connect(path)
        connection.sendall(f"{port}\n".encode("ascii"))
        if _read_answer(connection) != b"ok":
            connection.close()
            return None
        connection.settimeout(None)
        return connection
    except OSError:
        connection.close()
        return None


def _split_authority(authority: str) -> tuple[str, int]:
    """`host:port` as a pair, with IPv6 brackets kept on the host.

    Kept rather than stripped because the bracketed form is what
    `LOOPBACK_NAMES` is written against and what `getaddrinfo` is happy to be
    given back.
    """
    if authority.startswith("["):
        host, _, rest = authority.partition("]")
        host = f"{host}]"
        port = rest.lstrip(":")
    else:
        host, _, port = authority.rpartition(":")
        if not host:
            host, port = authority, ""
    return host, int(port) if port.isdigit() else 80


def _connect(host: str, port: int, timeout: float) -> socket.socket | None:
    target = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        return socket.create_connection((target, port), timeout=timeout)
    except OSError:
        return None


def open_upstream(host: str, port: int) -> tuple[socket.socket | None, str]:
    """Where this request should actually go, and which machine answered.

    Returns the connected socket and one of `sandbox`, `host` or `none`. The
    name is for the log line: "it went to the other machine" is the single
    most useful thing to know when somebody is surprised by what they got.
    """
    if host.lower() not in LOOPBACK_NAMES:
        connection = _connect(host, port, HOST_CONNECT_TIMEOUT)
        return connection, "direct" if connection else "none"

    local = _connect("127.0.0.1", port, LOCAL_CONNECT_TIMEOUT)
    if local is not None:
        return local, "sandbox"

    remote = open_relay(port)
    return remote, "host" if remote else "none"


def _splice(a: socket.socket, b: socket.socket) -> None:
    """Carry bytes both ways until either end is done.

    One selector over both sockets rather than a thread each way: a browsing
    agent opens a lot of these, and two threads per connection in a 2-vCPU
    sandbox is the kind of cost that only shows up under the load nobody
    tested with.
    """
    selector = selectors.DefaultSelector()
    a.setblocking(False)
    b.setblocking(False)
    selector.register(a, selectors.EVENT_READ, b)
    selector.register(b, selectors.EVENT_READ, a)
    open_ends = 2
    try:
        while open_ends:
            for key, _ in selector.select(timeout=300):
                source: socket.socket = key.fileobj  # type: ignore[assignment]
                sink: socket.socket = key.data
                try:
                    chunk = source.recv(_CHUNK)
                except OSError as error:
                    if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                        continue
                    chunk = b""
                if not chunk:
                    selector.unregister(source)
                    open_ends -= 1
                    # Half-close rather than tear down: a request whose body is
                    # finished still has a response coming back the other way.
                    try:
                        sink.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    continue
                try:
                    sink.sendall(chunk)
                except OSError:
                    return
    finally:
        selector.close()


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        client: socket.socket = self.request
        client.settimeout(30)
        try:
            head = self._read_head(client)
        except OSError:
            return
        if not head:
            return
        line, _, rest = head.partition(b"\r\n")
        try:
            method, target, _version = line.decode("latin-1").split(" ", 2)
        except ValueError:
            return

        if method.upper() == "CONNECT":
            host, port = _split_authority(target)
            upstream, where = open_upstream(host, port)
            if upstream is None:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                return
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        else:
            parsed = urlsplit(target)
            if not parsed.netloc:
                # A relative target means somebody pointed a normal client at
                # this port. It is a proxy, and saying so is better than
                # half-answering.
                client.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                return
            host, port = _split_authority(parsed.netloc)
            if parsed.scheme == "https" and port == 80:
                port = 443
            upstream, where = open_upstream(host, port)
            if upstream is None:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                return
            # Forwarded as an origin-form request, because what is on the other
            # end is an ordinary server rather than another proxy.
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
            head = f"{method} {path} HTTP/1.1\r\n".encode("latin-1") + rest
            upstream.sendall(head)
        del where
        try:
            _splice(client, upstream)
        finally:
            # The client is closed by the server; this end is ours. Left to the
            # collector, a browsing agent's worth of these holds relay slots
            # on the Mac open long after the page is done.
            upstream.close()


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _read_head(sock: socket.socket) -> bytes:
    buffered = b""
    while b"\r\n\r\n" not in buffered:
        chunk = sock.recv(_CHUNK)
        if not chunk:
            return b""
        buffered += chunk
        if len(buffered) > 64 * 1024:
            return b""
    return buffered


_Handler._read_head = staticmethod(_read_head)  # type: ignore[attr-defined]


def serve(port: int, *, ready: threading.Event | None = None) -> None:
    server = _Server(("127.0.0.1", port), _Handler)
    if ready is not None:
        ready.set()
    server.serve_forever()


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 4851
    serve(port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
