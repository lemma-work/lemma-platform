"""A byte stream to a port on a connected machine, from any replica.

The replica holding the machine's link opens a mux stream on it. Any other
replica reads the link's route from Redis and connects to the holder's
internal stream route, which opens the mux stream there and splices; to the
caller both are the same ``ByteStream``. No route, or a route naming a link
that is gone, is ``MachineNotConnected`` -- never a fallback to some other
machine.
"""

from __future__ import annotations

import asyncio
from typing import Protocol
from urllib.parse import quote
from uuid import UUID


from app.core.log.log import get_logger
from app.modules.agent.contracts.compute_link import (
    AGENT_HOST_LINK_HEARTBEAT_MS,
    AGENT_HOST_LINK_MISSED_HEARTBEATS,
)
from app.modules.workspace.domain.machine_errors import MachineNotConnected
from app.modules.workspace.services.machine_link.routes import (
    INTERNAL_STREAM_PATH,
    PROXY_NOT_HELD,
    PROXY_REFUSED,
    LinkRoutes,
    read_small_json,
    replica_url,
    signed_headers,
    small_json,
)
from app.modules.workspace.services.machine_link.session import (
    MachineLinkRegistry,
)
from sandbox_runtime.machine_link.mux import (
    MAX_DATA_BYTES,
    ResetCode,
    StreamReset,
)

logger = get_logger(__name__)

#: How long connecting to the holder replica may take.
_PROXY_OPEN_TIMEOUT_SECONDS = 10.0


class ByteStream(Protocol):
    async def read(self, max_bytes: int = -1) -> bytes: ...

    async def write(self, data: bytes) -> None: ...

    async def write_eof(self) -> None: ...

    async def aclose(self) -> None: ...


machine_link_registry = MachineLinkRegistry()
#: A route outlives its last refresh by as long as a silent link is kept.
link_routes = LinkRoutes(
    ttl_seconds=AGENT_HOST_LINK_HEARTBEAT_MS * AGENT_HOST_LINK_MISSED_HEARTBEATS / 1000
)


async def open_machine_stream(
    connection_id: UUID,
    target: dict[str, object],
    *,
    name: str | None = None,
    registry: MachineLinkRegistry | None = None,
    routes: LinkRoutes | None = None,
    here: str | None = None,
) -> ByteStream:
    """A stream to ``target`` on the machine, wherever its link is held."""
    label = name or str(connection_id)
    registry = machine_link_registry if registry is None else registry
    local = registry.get(connection_id)
    if local is not None:
        try:
            return await local.open_stream(target)
        except StreamReset as reset:
            if reset.code == ResetCode.LINK_LOST:
                raise MachineNotConnected(label) from reset
            raise
    route = await (link_routes if routes is None else routes).read(connection_id)
    # A route naming this replica names a link that is gone from it.
    if route is None or route.url == (here or replica_url()):
        raise MachineNotConnected(label)
    return await RemoteStream.connect(route.url, connection_id, target, name=label)


class RemoteStream:
    """A stream spliced through the replica that holds the machine's link.

    Binary messages are the stream's bytes; the two text messages
    ``{"fin": true}`` and ``{"reset": {...}}`` carry its half close and its
    reset, so the far side sees exactly what a local mux stream would show.
    """

    def __init__(self, socket, *, name: str) -> None:
        self._socket = socket
        self._name = name
        self._buffer = bytearray()
        self._eof = False
        self._reset: StreamReset | None = None

    @classmethod
    async def connect(
        cls, base_url: str, connection_id: UUID, target: dict[str, object], *, name: str
    ) -> RemoteStream:
        import websockets
        from websockets.exceptions import ConnectionClosed, InvalidStatus

        scheme = "wss" if base_url.startswith("https") else "ws"
        host = base_url.split("://", 1)[-1].rstrip("/")
        path = INTERNAL_STREAM_PATH.format(connection_id=connection_id)
        url = f"{scheme}://{host}{path}?target={quote(small_json(target))}"
        try:
            socket = await asyncio.wait_for(
                websockets.connect(
                    url,
                    additional_headers=signed_headers(connection_id),
                    compression=None,
                    max_size=MAX_DATA_BYTES * 2,
                    open_timeout=_PROXY_OPEN_TIMEOUT_SECONDS,
                    ping_interval=None,
                ),
                _PROXY_OPEN_TIMEOUT_SECONDS,
            )
        except (OSError, TimeoutError, InvalidStatus) as exc:
            logger.warning(
                "workspace.machine_link.proxy_unreachable",
                connection_id=str(connection_id),
                replica=base_url,
                error_type=type(exc).__name__,
            )
            raise MachineNotConnected(name) from exc
        stream = cls(socket, name=name)
        try:
            first = await socket.recv()
        except ConnectionClosed as closed:
            stream._closed_by_holder(closed)
            raise stream._reset or MachineNotConnected(name) from closed
        if first != '{"accept":true}':
            await socket.close()
            raise MachineNotConnected(name)
        return stream

    def _closed_by_holder(self, closed) -> None:
        code = closed.rcvd.code if closed.rcvd is not None else None
        reason = closed.rcvd.reason if closed.rcvd is not None else ""
        if code == PROXY_NOT_HELD:
            raise MachineNotConnected(self._name) from closed
        if code == PROXY_REFUSED:
            detail = read_small_json(reason) if reason.startswith("{") else {}
            self._reset = StreamReset(
                str(detail.get("code") or ResetCode.REFUSED),
                str(detail.get("message") or ""),
            )
            return
        self._reset = StreamReset(ResetCode.LINK_LOST, "the holder replica went away")

    async def read(self, max_bytes: int = -1) -> bytes:
        from websockets.exceptions import ConnectionClosed

        while not self._buffer:
            if self._reset is not None:
                raise self._reset
            if self._eof:
                return b""
            try:
                message = await self._socket.recv()
            except ConnectionClosed as closed:
                if self._eof:
                    return b""
                self._closed_by_holder(closed)
                continue
            if isinstance(message, bytes):
                self._buffer.extend(message)
                continue
            control = read_small_json(message)
            if control.get("fin"):
                self._eof = True
            elif isinstance(control.get("reset"), dict):
                self._reset = StreamReset(
                    str(control["reset"].get("code") or ResetCode.CANCELLED),
                    str(control["reset"].get("message") or ""),
                )
        if max_bytes < 0 or max_bytes >= len(self._buffer):
            chunk = bytes(self._buffer)
            self._buffer.clear()
        else:
            chunk = bytes(self._buffer[:max_bytes])
            del self._buffer[:max_bytes]
        return chunk

    async def write(self, data: bytes) -> None:
        from websockets.exceptions import ConnectionClosed

        if self._reset is not None:
            raise self._reset
        try:
            for offset in range(0, len(data), MAX_DATA_BYTES):
                await self._socket.send(data[offset : offset + MAX_DATA_BYTES])
        except ConnectionClosed as closed:
            self._closed_by_holder(closed)
            raise self._reset or StreamReset(ResetCode.LINK_LOST) from closed

    async def write_eof(self) -> None:
        from websockets.exceptions import ConnectionClosed

        try:
            await self._socket.send('{"fin":true}')
        except ConnectionClosed:
            pass

    async def aclose(self) -> None:
        await self._socket.close()
