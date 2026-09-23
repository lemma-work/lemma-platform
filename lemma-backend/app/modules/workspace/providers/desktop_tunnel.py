"""Connections to Lemma Desktop sandboxes, over the guest's vsock tunnel.

The guest publishes each sandbox's runtime, browser relay and apps on its own
network address. Dialling that from the host is a connection to a device on
the local network, which macOS allows per executable, through a permission a
background process is never prompted for. The backend is one, and its
executable changes with every release, so each update could silently cut it off
from every sandbox. `lemma-vz` bridges a guest vsock port to a unix socket
instead; guestd's end reads the port wanted, answers `ok`, and splices.

Only addresses the guest itself reported are sent this way, so Docker, E2B and
anything else keep their own routes.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpcore
import httpx

from app.modules.workspace.config import workspace_settings

if TYPE_CHECKING:
    from collections.abc import Iterable

#: What guestd sends before splicing, and the longest answer worth reading.
_ACCEPTED = b"ok\n"
_MAX_ANSWER_BYTES = 256
#: Covers the vsock connect inside the guest, which is quick when it works.
_HANDSHAKE_TIMEOUT_SECONDS = 10.0

#: The guest's addresses, as it reported them. One guest, so a handful at most.
#: An address rather than cached data: worthless to another process, which is
#: why it is held here and not in Redis.
_guest_hosts: set[str] = set()


class TunnelRefused(httpcore.ConnectError, OSError):
    """The guest would not open a tunnel to that port, and said why.

    Both kinds of connection failure: `httpx` translates only `httpcore`'s own
    errors into `httpx.ConnectError`, which is what every sandbox client
    catches, and `websockets` callers expect an `OSError`.
    """


def remember_guest_address(url: object) -> None:
    """Note an address the Desktop guest reported for one of its sandboxes."""
    if isinstance(url, str) and url:
        host = urlsplit(url).hostname
        if host:
            _guest_hosts.add(host)


def tunnel_socket() -> str | None:
    """The unix socket to the guest's tunnel, when this is Lemma Desktop on macOS."""
    return workspace_settings.local_tunnel_socket or None


def _forget_guest_addresses_for_tests(hosts: Iterable[str] | None = None) -> None:
    """Reset what was learned. Tests only; the set is process-wide."""
    if hosts is None:
        _guest_hosts.clear()
    else:
        _guest_hosts.difference_update(hosts)


async def _handshake(read, write, port: int) -> None:
    """The bridge's ready byte, then guestd's one-line protocol."""
    ready = await read(1)
    if ready != b"\x00":
        raise TunnelRefused("the Desktop runtime bridge did not accept the connection")
    await write(f"{port}\n".encode())
    # Byte by byte, so nothing after the answer is consumed here: guestd sends
    # nothing more until the client speaks, but reading past the newline would
    # still be taking bytes that belong to the stream.
    answer = b""
    while not answer.endswith(b"\n"):
        if len(answer) >= _MAX_ANSWER_BYTES:
            raise TunnelRefused("the Desktop guest's tunnel answer was too long")
        chunk = await read(1)
        if not chunk:
            raise TunnelRefused("the Desktop guest closed the tunnel before answering")
        answer += chunk
    if answer != _ACCEPTED:
        reason = answer.decode(errors="replace").strip().removeprefix("error ").strip()
        raise TunnelRefused(f"the Desktop guest could not reach port {port}: {reason}")


class _TunnelNetworkBackend(httpcore.AsyncNetworkBackend):
    """`httpcore`'s network backend, with guest addresses sent through the tunnel."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._network = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ) -> httpcore.AsyncNetworkStream:
        if host not in _guest_hosts:
            return await self._network.connect_tcp(
                host,
                port,
                timeout=timeout,
                local_address=local_address,
                socket_options=socket_options,
            )
        stream = await self._network.connect_unix_socket(self._path, timeout=timeout)
        async with contextlib.AsyncExitStack() as on_failure:
            on_failure.push_async_callback(stream.aclose)
            await _handshake(
                lambda size: stream.read(size, timeout=timeout),
                lambda data: stream.write(data, timeout=timeout),
                port,
            )
            on_failure.pop_all()
        return stream

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options=None
    ) -> httpcore.AsyncNetworkStream:
        return await self._network.connect_unix_socket(
            path, timeout=timeout, socket_options=socket_options
        )

    async def sleep(self, seconds: float) -> None:
        await self._network.sleep(seconds)


class _TunnelTransport(httpx.AsyncHTTPTransport):
    """`httpx`'s transport over a connection pool that knows the tunnel."""

    def __init__(self, path: str) -> None:
        super().__init__()
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=5.0,
            network_backend=_TunnelNetworkBackend(path),
        )


def sandbox_transport() -> httpx.AsyncBaseTransport | None:
    """The transport for a client that may address a Desktop sandbox.

    `None` everywhere but Desktop on macOS, so a caller passes it straight to
    `httpx.AsyncClient(transport=...)` and gets httpx's own default otherwise.
    """
    path = tunnel_socket()
    return transport_for(path) if path is not None else None


def transport_for(socket_path: str) -> httpx.AsyncBaseTransport:
    """A transport sending guest addresses through the tunnel at `socket_path`."""
    return _TunnelTransport(socket_path)


async def tunneled_socket(
    url: str, *, socket_path: str | None = None
) -> socket.socket | None:
    """A socket already through the tunnel to `url`'s port, or `None` if `url`
    is not a Desktop guest address or there is no tunnel.

    For clients that take a connected socket, like `websockets.connect(sock=...)`.
    `socket_path` defaults to the configured tunnel.
    """
    parts = urlsplit(url)
    host = parts.hostname
    path = socket_path if socket_path is not None else tunnel_socket()
    if path is None or host is None or host not in _guest_hosts:
        return None
    port = parts.port or (443 if parts.scheme in ("https", "wss") else 80)
    loop = asyncio.get_running_loop()
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.setblocking(False)
    with contextlib.ExitStack() as on_failure:
        on_failure.callback(connection.close)
        try:
            # A bridge that accepts and never answers must not hold a viewer
            # open forever; `websockets`' own open timeout starts after this.
            async with asyncio.timeout(_HANDSHAKE_TIMEOUT_SECONDS):
                await loop.sock_connect(connection, path)
                await _handshake(
                    lambda size: loop.sock_recv(connection, size),
                    lambda data: loop.sock_sendall(connection, data),
                    port,
                )
        except TimeoutError as exc:
            raise TunnelRefused(
                f"the Desktop guest did not open a tunnel to port {port} in time"
            ) from exc
        on_failure.pop_all()
    return connection
