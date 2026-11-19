"""Connected machines as ordinary network addresses, for unchanged clients.

A connected machine's provider names its runtime, relay and ports as
``http://<connection id>.machine.lemma.internal:<port>``. Nothing resolves
that name: the sandbox transport (``desktop_tunnel.sandbox_transport``) and
``tunneled_socket`` recognise the suffix and open a stream on the machine's
link instead, the way they already send a Desktop guest's addresses down its
vsock tunnel. So ``WorkspaceRuntimeClient``, the port proxy and the browser
relay bridge reach a machine without knowing it is one.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from uuid import UUID

import anyio
import httpcore

from app.modules.workspace.domain.machine_errors import MachineNotConnected
from sandbox_runtime.machine_link.mux import MAX_DATA_BYTES, StreamReset

#: The suffix every connected machine's address ends in. ``.internal`` is
#: reserved for private use, so it can never be someone's real host.
MACHINE_HOST_SUFFIX = ".machine.lemma.internal"


def machine_host(connection_id: UUID) -> str:
    return f"{connection_id.hex}{MACHINE_HOST_SUFFIX}"


def machine_url(connection_id: UUID, port: int, *, scheme: str = "http") -> str:
    return f"{scheme}://{machine_host(connection_id)}:{port}"


def connection_for_host(host: str | None) -> UUID | None:
    """The connection a machine address names, or None for any other host."""
    if not host or not host.endswith(MACHINE_HOST_SUFFIX):
        return None
    try:
        return UUID(hex=host.removesuffix(MACHINE_HOST_SUFFIX))
    except ValueError:
        return None


async def _open(connection_id: UUID, port: int):
    from app.modules.workspace.services.machine_link.dialer import (
        open_machine_stream,
    )

    return await open_machine_stream(connection_id, {"kind": "port", "port": port})


class MachineNetworkStream(httpcore.AsyncNetworkStream):
    """One mux stream, as the connection httpcore reads and writes."""

    def __init__(self, stream) -> None:
        self._stream = stream

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        try:
            with anyio.fail_after(timeout):
                return await self._stream.read(max_bytes)
        except TimeoutError as exc:
            raise httpcore.ReadTimeout(str(exc)) from exc
        except StreamReset as exc:
            raise httpcore.ReadError(str(exc)) from exc

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        if not buffer:
            return
        try:
            with anyio.fail_after(timeout):
                await self._stream.write(buffer)
        except TimeoutError as exc:
            raise httpcore.WriteTimeout(str(exc)) from exc
        except StreamReset as exc:
            raise httpcore.WriteError(str(exc)) from exc

    async def aclose(self) -> None:
        await self._stream.aclose()

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        raise httpcore.ConnectError("a machine's link is already encrypted end to end")

    def get_extra_info(self, info: str):
        return None


class MachineUnreachable(httpcore.ConnectError, OSError):
    """``MachineNotConnected`` in the shape every sandbox client already
    catches: ``httpx`` turns ``httpcore.ConnectError`` into its own, and a
    ``websockets`` caller expects an ``OSError``. The sentence is kept."""


async def connect_machine(host: str, port: int, timeout: float | None):
    connection_id = connection_for_host(host)
    assert connection_id is not None
    try:
        with anyio.fail_after(timeout):
            return MachineNetworkStream(await _open(connection_id, port))
    except MachineNotConnected as exc:
        raise MachineUnreachable(str(exc)) from exc
    except StreamReset as exc:
        raise httpcore.ConnectError(f"the machine refused port {port}: {exc}") from exc
    except TimeoutError as exc:
        raise httpcore.ConnectTimeout(str(exc)) from exc


async def machine_socket(host: str, port: int) -> socket.socket:
    """A connected socket whose far end is a stream to the machine's port.

    For clients that only take a socket (``websockets.connect(sock=...)``): one
    end of a socket pair is returned, and a task splices the other end to the
    stream for as long as either side is open.
    """
    connection_id = connection_for_host(host)
    assert connection_id is not None
    try:
        stream = await _open(connection_id, port)
    except MachineNotConnected as exc:
        raise MachineUnreachable(str(exc)) from exc
    except StreamReset as exc:
        raise MachineUnreachable(f"the machine refused port {port}: {exc}") from exc
    ours, theirs = socket.socketpair()
    ours.setblocking(False)
    theirs.setblocking(False)
    task = asyncio.ensure_future(_splice_socket(stream, ours))
    _splices.add(task)
    task.add_done_callback(_splices.discard)
    return theirs


#: The splices running now, held so none is collected mid-copy; each removes
#: itself when it ends, and there are at most as many as open streams.
_splices: set[asyncio.Task[None]] = set()  # memory: bounded by open mux streams


async def _splice_socket(stream, end: socket.socket) -> None:
    loop = asyncio.get_running_loop()

    async def outbound() -> None:
        while chunk := await loop.sock_recv(end, MAX_DATA_BYTES):
            await stream.write(chunk)
        await stream.write_eof()

    async def inbound() -> None:
        while chunk := await stream.read(MAX_DATA_BYTES):
            await loop.sock_sendall(end, chunk)
        with contextlib.suppress(OSError):
            end.shutdown(socket.SHUT_WR)

    tasks = [asyncio.ensure_future(outbound()), asyncio.ensure_future(inbound())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in tasks:
            if task.done() and not task.cancelled() and task.exception() is not None:
                break
        else:
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        end.close()
        await stream.aclose()
