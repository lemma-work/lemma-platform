"""Desktop sandbox connections over the guest's vsock tunnel.

A fake bridge stands in for `lemma-vz` plus guestd: the ready byte, one line
naming the port, `ok`, then the stream. What is under test is that guest
addresses take this route, that nothing else does, and that a refusal arrives
as the connection error every sandbox client already handles.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import httpx
import pytest

from app.modules.workspace.config import workspace_settings
from app.modules.workspace.providers import desktop_tunnel

pytestmark = pytest.mark.asyncio

GUEST = "192.168.64.2"


class _FakeBridge:
    """A unix socket that answers like the guest's tunnel."""

    def __init__(self, *, refuse: str | None = None) -> None:
        self.refuse = refuse
        self.ports: list[int] = []
        # Short: macOS caps a unix socket path at 104 bytes.
        self.path = str(Path(tempfile.mkdtemp(prefix="lt", dir="/tmp")) / "t.sock")
        self._server: asyncio.base_events.Server | None = None

    async def __aenter__(self) -> _FakeBridge:
        self._server = await asyncio.start_unix_server(self._serve, path=self.path)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        writer.write(b"\x00")
        await writer.drain()
        port = int((await reader.readline()).strip())
        self.ports.append(port)
        if self.refuse:
            writer.write(f"error {self.refuse}\n".encode())
            await writer.drain()
            writer.close()
            return
        writer.write(b"ok\n")
        await writer.drain()
        request = await reader.readuntil(b"\r\n\r\n")
        body = b"through the tunnel" if request.startswith(b"GET /health") else b"?"
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s"
            % (len(body), body)
        )
        await writer.drain()
        writer.close()


@pytest.fixture(autouse=True)
def _clean():
    desktop_tunnel._forget_guest_addresses_for_tests()
    yield
    desktop_tunnel._forget_guest_addresses_for_tests()


async def test_a_guest_address_is_reached_through_the_tunnel():
    async with _FakeBridge() as bridge:
        desktop_tunnel.remember_guest_address(f"http://{GUEST}:49153")

        async with httpx.AsyncClient(
            transport=desktop_tunnel.transport_for(bridge.path)
        ) as client:
            response = await client.get(f"http://{GUEST}:49153/health")

    assert response.status_code == 200
    assert response.text == "through the tunnel"
    assert bridge.ports == [49153], "the guest was asked for the URL's own port"


async def test_an_address_the_guest_never_reported_keeps_its_own_route():
    """Docker, E2B and the cloud share these clients; none of them tunnels."""
    served: list[str] = []

    async def plain(reader, writer):
        served.append((await reader.readuntil(b"\r\n\r\n")).decode().split()[1])
        writer.write(b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(plain, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async with _FakeBridge() as bridge:
            desktop_tunnel.remember_guest_address(f"http://{GUEST}:49153")
            async with httpx.AsyncClient(
                transport=desktop_tunnel.transport_for(bridge.path)
            ) as client:
                response = await client.get(f"http://127.0.0.1:{port}/plain")
            assert bridge.ports == []
    finally:
        server.close()
        await server.wait_closed()
    assert response.status_code == 204
    assert served == ["/plain"]


async def test_without_the_setting_nothing_changes():
    assert workspace_settings.local_tunnel_socket is None, (
        "unset unless Desktop sets it"
    )
    desktop_tunnel.remember_guest_address(f"http://{GUEST}:49153")
    assert desktop_tunnel.sandbox_transport() is None
    assert await desktop_tunnel.tunneled_socket(f"ws://{GUEST}:49153/x") is None


async def test_a_refusal_is_the_connection_error_sandbox_clients_handle():
    async with _FakeBridge(refuse="connection refused") as bridge:
        desktop_tunnel.remember_guest_address(f"http://{GUEST}:49160")
        async with httpx.AsyncClient(
            transport=desktop_tunnel.transport_for(bridge.path)
        ) as client:
            with pytest.raises(httpx.ConnectError) as caught:
                await client.get(f"http://{GUEST}:49160/health")
    assert "port 49160" in str(caught.value)
    assert "connection refused" in str(caught.value)


async def test_a_websocket_gets_a_socket_already_through_the_tunnel():
    async with _FakeBridge() as bridge:
        desktop_tunnel.remember_guest_address(f"http://{GUEST}:49154")

        connection = await desktop_tunnel.tunneled_socket(
            f"ws://{GUEST}:49154/vnc", socket_path=bridge.path
        )
        assert connection is not None
        loop = asyncio.get_running_loop()
        await loop.sock_sendall(connection, b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")
        reply = await loop.sock_recv(connection, 256)
        connection.close()

    assert bridge.ports == [49154]
    assert reply.startswith(b"HTTP/1.1 200")


async def test_every_address_in_a_guest_status_is_remembered():
    from app.modules.workspace.providers.lemma_local_ops import _status_object

    _status_object(
        {
            "status": {
                "runtime_url": f"http://{GUEST}:49153",
                "apps": {"browser": {"private_url": f"http://{GUEST}:49154"}},
            }
        }
    )
    assert GUEST in desktop_tunnel._guest_hosts


async def test_a_bridge_that_never_answers_is_refused_not_awaited_forever():
    async def silent(reader, writer):
        await reader.read()

    path = str(Path(tempfile.mkdtemp(prefix="lt", dir="/tmp")) / "s.sock")
    server = await asyncio.start_unix_server(silent, path=path)
    desktop_tunnel.remember_guest_address(f"http://{GUEST}:49155")
    try:
        desktop_tunnel._HANDSHAKE_TIMEOUT_SECONDS = 0.1
        with pytest.raises(desktop_tunnel.TunnelRefused, match="in time"):
            await desktop_tunnel.tunneled_socket(
                f"ws://{GUEST}:49155/x", socket_path=path
            )
    finally:
        desktop_tunnel._HANDSHAKE_TIMEOUT_SECONDS = 10.0
        server.close()
        await server.wait_closed()
