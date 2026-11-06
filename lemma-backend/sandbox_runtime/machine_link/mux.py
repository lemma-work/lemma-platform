"""Byte streams multiplexed over one message channel, with flow control.

A connected machine reaches Lemma over one outbound WebSocket, the Agent Host
link in its ``compute`` role (docs/architecture/connected-machines.md). Every
connection Lemma makes into the machine -- an HTTP request to the workspace
runtime, the browser relay's view socket, a preview of a dev server -- is a
*stream* on that one socket. This module is both ends of that: the backend
opens streams, the machine's agent accepts them and splices each to a local TCP
port. Standard library only, because the agent runs it from the runtime overlay.

Every mux frame is one binary WebSocket message::

    type (u8) | stream id (u32, big endian) | payload

========  ====================================================================
``OPEN``    ``{"target": {...}, "window": n}`` -- open a stream
``ACCEPT``  ``{"window": n}`` -- the stream is open
``DATA``    up to ``MAX_DATA_BYTES`` of the stream's bytes
``WINDOW``  u32: the receiver consumed this many more bytes; send that many more
``FIN``     no payload: the sender writes nothing more (half close)
``RESET``   ``{"code": ..., "message": ...}`` -- the stream is over, both ways
========  ====================================================================

**Flow control** is per stream and credit based, as in HTTP/2. Each side
advertises a receive window when the stream opens; a sender never has more
unacknowledged bytes in flight than the peer's window, and the receiver hands
credit back with ``WINDOW`` only as the stream's *consumer* reads. So a slow
consumer -- a browser tab that stopped reading a preview -- stops its own
stream's sender and nothing else, and what a stream can buffer is bounded by
its window. A peer that sends past the window it was given has broken the
protocol, and the stream is reset with ``flow_control``.

**Backpressure** on the socket itself is the caller's ``send``: it is awaited
for every frame, so a link whose socket is full slows every writer on it rather
than buffering without bound.

A stream ends when both sides have sent ``FIN``, or when either sends
``RESET``. Losing the link resets every stream on it with ``link_lost``.
"""

from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import Awaitable, Callable
from typing import Literal

OPEN = 1
ACCEPT = 2
DATA = 3
WINDOW = 4
FIN = 5
RESET = 6

FRAME_TYPES = {
    OPEN: "open",
    ACCEPT: "accept",
    DATA: "data",
    WINDOW: "window",
    FIN: "fin",
    RESET: "reset",
}

_HEADER = struct.Struct(">BI")
HEADER_BYTES = _HEADER.size

#: The most one ``DATA`` frame carries. Small enough that one stream's frame
#: never holds the socket long enough to delay another stream's.
MAX_DATA_BYTES = 64 * 1024

#: What each side lets a peer send before it has read anything.
DEFAULT_WINDOW_BYTES = 256 * 1024

#: The largest window either side may advertise. Bounds what one stream can
#: make the other side buffer.
MAX_WINDOW_BYTES = 16 * 1024 * 1024

#: Streams one link may hold open at once. A browser view, a few HTTP
#: connections and their previews are tens; past this is a runaway.
MAX_STREAMS = 512

#: The largest control payload (``OPEN``, ``ACCEPT``, ``RESET``) accepted.
MAX_CONTROL_BYTES = 8 * 1024

#: How long ``open`` waits for the peer to accept before giving up.
DEFAULT_OPEN_TIMEOUT_SECONDS = 15.0


class ResetCode:
    """Why a stream was reset. Plain strings: they travel as JSON."""

    #: The machine refused the target (a port nothing listens on, a target kind
    #: it does not serve).
    REFUSED = "refused"
    #: One side closed the stream before the other finished.
    CANCELLED = "cancelled"
    #: The peer sent more than the window it was given.
    FLOW_CONTROL = "flow_control"
    #: A frame that could not be read.
    PROTOCOL = "protocol"
    #: The link under the stream went away.
    LINK_LOST = "link_lost"
    #: Too many streams already open.
    TOO_MANY_STREAMS = "too_many_streams"
    #: Something failed on the side that reset.
    INTERNAL = "internal"


class MuxError(Exception):
    """A frame that cannot be part of this protocol."""


class StreamReset(ConnectionError):
    """The stream was reset, by either side. ``code`` is a ``ResetCode``."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message


Send = Callable[[bytes], Awaitable[None]]
Accept = Callable[["MuxStream", dict[str, object]], Awaitable[None]]


def encode(frame_type: int, stream_id: int, payload: bytes = b"") -> bytes:
    return _HEADER.pack(frame_type, stream_id) + payload


def decode(frame: bytes) -> tuple[int, int, bytes]:
    if len(frame) < HEADER_BYTES:
        raise MuxError("a mux frame is shorter than its header")
    frame_type, stream_id = _HEADER.unpack_from(frame)
    if frame_type not in FRAME_TYPES:
        raise MuxError(f"unknown mux frame type {frame_type}")
    return frame_type, stream_id, bytes(frame[HEADER_BYTES:])


def _control(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


def _read_control(payload: bytes) -> dict[str, object]:
    if len(payload) > MAX_CONTROL_BYTES:
        raise MuxError("a mux control payload is too large")
    try:
        value = json.loads(payload or b"{}")
    except ValueError:
        raise MuxError("a mux control payload is not JSON") from None
    if not isinstance(value, dict):
        raise MuxError("a mux control payload is not an object")
    return value


def _window(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise MuxError("a window must be an integer")
    if value < 1 or value > MAX_WINDOW_BYTES:
        raise MuxError(f"a window must be 1..{MAX_WINDOW_BYTES} bytes")
    return value


class MuxStream:
    """One byte stream. Read with ``read``, write with ``write``."""

    def __init__(
        self, mux: Mux, stream_id: int, *, target: dict[str, object], window: int
    ) -> None:
        self._mux = mux
        self.stream_id = stream_id
        self.target = target
        #: What this side advertised, and so the most the peer may have in
        #: flight to it.
        self._receive_window = window
        self._buffer = bytearray()
        #: Bytes received and not yet handed back as credit.
        self._unacknowledged = 0
        self._readable = asyncio.Event()
        self._remote_fin = False
        #: What the peer lets this side send right now.
        self._credit = 0
        self._writable = asyncio.Event()
        self._local_fin = False
        self._reset: StreamReset | None = None
        self._write_lock = asyncio.Lock()
        self._closed = asyncio.Event()

    # ---------------------------------------------------------------- reading

    async def read(self, max_bytes: int = -1) -> bytes:
        """Up to ``max_bytes`` (all buffered when -1); ``b""`` once the peer
        has finished. Raises ``StreamReset`` if the stream was reset."""
        while not self._buffer:
            if self._reset is not None:
                raise self._reset
            if self._remote_fin:
                return b""
            self._readable.clear()
            await self._readable.wait()
        if max_bytes < 0 or max_bytes >= len(self._buffer):
            chunk = bytes(self._buffer)
            self._buffer.clear()
        else:
            chunk = bytes(self._buffer[:max_bytes])
            del self._buffer[:max_bytes]
        await self._consumed(len(chunk))
        self._maybe_finished()
        return chunk

    async def _consumed(self, count: int) -> None:
        """Hand credit back once a meaningful part of the window was read.

        Half the window, so the sender is told before it runs dry and a busy
        stream does not cost one ``WINDOW`` frame per ``DATA`` frame.
        """
        self._unacknowledged += count
        if self._reset is not None or self._remote_fin:
            return
        if self._unacknowledged >= max(1, self._receive_window // 2):
            increment, self._unacknowledged = self._unacknowledged, 0
            await self._mux.send_frame(
                WINDOW, self.stream_id, struct.pack(">I", increment)
            )

    def _received(self, data: bytes) -> None:
        if self._remote_fin:
            raise MuxError("data after fin")
        pending = len(self._buffer) + self._unacknowledged + len(data)
        if pending > self._receive_window:
            raise StreamReset(
                ResetCode.FLOW_CONTROL, "the peer sent more than its window"
            )
        self._buffer.extend(data)
        self._readable.set()

    # ---------------------------------------------------------------- writing

    async def write(self, data: bytes) -> None:
        """Send ``data``, waiting for credit as the peer reads."""
        view = memoryview(data)
        async with self._write_lock:
            while view:
                if self._reset is not None:
                    raise self._reset
                if self._local_fin:
                    raise StreamReset(ResetCode.PROTOCOL, "write after write_eof")
                if self._credit <= 0:
                    self._writable.clear()
                    await self._writable.wait()
                    continue
                size = min(len(view), self._credit, MAX_DATA_BYTES)
                self._credit -= size
                await self._mux.send_frame(DATA, self.stream_id, bytes(view[:size]))
                view = view[size:]

    async def write_eof(self) -> None:
        """Tell the peer nothing more is coming; reading still works."""
        async with self._write_lock:
            if self._local_fin or self._reset is not None:
                return
            self._local_fin = True
            await self._mux.send_frame(FIN, self.stream_id)
        self._maybe_finished()

    def _granted(self, increment: int) -> None:
        self._credit += increment
        if self._credit > MAX_WINDOW_BYTES:
            raise StreamReset(ResetCode.FLOW_CONTROL, "the peer granted too much")
        self._writable.set()

    # ---------------------------------------------------------------- ending

    @property
    def is_closed(self) -> bool:
        return self._closed.is_set()

    async def wait_closed(self) -> None:
        await self._closed.wait()

    async def reset(self, code: str = ResetCode.CANCELLED, message: str = "") -> None:
        """End the stream both ways now. Idempotent."""
        if self._closed.is_set():
            return
        self._terminate(StreamReset(code, message))
        await self._mux.send_frame(
            RESET, self.stream_id, _control({"code": code, "message": message})
        )

    async def aclose(self) -> None:
        """Close as a socket would: finished if both sides were, else reset."""
        if self._closed.is_set():
            return
        if self._local_fin and self._remote_fin:
            self._terminate(None)
            return
        await self.reset(ResetCode.CANCELLED, "closed")

    def _remote_finished(self) -> None:
        self._remote_fin = True
        self._readable.set()
        self._maybe_finished()

    def _maybe_finished(self) -> None:
        if self._local_fin and self._remote_fin and not self._buffer:
            # Not ``_terminate``: a finished stream reads ``b""``, not a reset.
            self._closed.set()
            self._mux._forget(self.stream_id)

    def _terminate(self, reason: StreamReset | None) -> None:
        if reason is not None and self._reset is None:
            self._reset = reason
        self._readable.set()
        self._writable.set()
        self._closed.set()
        self._mux._forget(self.stream_id)


class Mux:
    """Every stream on one link.

    ``send`` writes one binary message to the link and is awaited for every
    frame; it must serialise its own writes. ``accept`` is called, in a task of
    its own, for every stream the peer opens -- ``None`` refuses them all.
    Which side opens odd ids and which even is fixed by ``role``, so ids the two
    sides choose never collide.
    """

    def __init__(
        self,
        send: Send,
        *,
        role: Literal["opener", "acceptor"],
        accept: Accept | None = None,
        window: int = DEFAULT_WINDOW_BYTES,
        max_streams: int = MAX_STREAMS,
    ) -> None:
        self._send = send
        self._accept = accept
        self._window = _window(window)
        self._max_streams = max_streams
        self._next_id = 1 if role == "opener" else 2
        self._streams: dict[int, MuxStream] = {}
        self._opening: dict[int, asyncio.Future[int]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed: StreamReset | None = None

    @property
    def stream_count(self) -> int:
        return len(self._streams)

    async def send_frame(self, frame_type: int, stream_id: int, payload=b"") -> None:
        if self._closed is not None and frame_type != RESET:
            raise self._closed
        if self._closed is not None:
            return
        await self._send(encode(frame_type, stream_id, payload))

    # ---------------------------------------------------------------- opening

    async def open(
        self,
        target: dict[str, object],
        *,
        timeout: float = DEFAULT_OPEN_TIMEOUT_SECONDS,
    ) -> MuxStream:
        """Open a stream to ``target``; raises ``StreamReset`` if refused."""
        if self._closed is not None:
            raise self._closed
        if len(self._streams) >= self._max_streams:
            raise StreamReset(ResetCode.TOO_MANY_STREAMS, "too many open streams")
        stream_id = self._next_id
        self._next_id += 2
        stream = MuxStream(self, stream_id, target=target, window=self._window)
        accepted: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        self._streams[stream_id] = stream
        self._opening[stream_id] = accepted
        try:
            await self._send(
                encode(
                    OPEN,
                    stream_id,
                    _control({"target": target, "window": self._window}),
                )
            )
            credit = await asyncio.wait_for(accepted, timeout)
        except BaseException:
            self._opening.pop(stream_id, None)
            if not stream.is_closed:
                stream._terminate(StreamReset(ResetCode.CANCELLED, "open abandoned"))
                if self._closed is None:
                    try:
                        await self._send(
                            encode(RESET, stream_id, _control({"code": "cancelled"}))
                        )
                    except Exception:
                        pass
            raise
        finally:
            self._opening.pop(stream_id, None)
        stream._granted(credit)
        return stream

    # ---------------------------------------------------------------- reading

    async def feed(self, frame: bytes) -> None:
        """Take one binary message off the link.

        Never waits on a consumer: data is buffered within the window and
        handed over as it is read. Raises ``MuxError`` for a frame that breaks
        the protocol as a whole, which should end the link.
        """
        frame_type, stream_id, payload = decode(frame)
        if frame_type == OPEN:
            await self._opened_by_peer(stream_id, payload)
            return
        stream = self._streams.get(stream_id)
        if stream is None:
            # Frames for a stream already reset cross the reset on the wire.
            return
        try:
            if frame_type == ACCEPT:
                self._accepted(stream_id, payload)
            elif frame_type == DATA:
                stream._received(payload)
            elif frame_type == WINDOW:
                if len(payload) != 4:
                    raise MuxError("a window frame carries four bytes")
                (increment,) = struct.unpack(">I", payload)
                stream._granted(increment)
            elif frame_type == FIN:
                stream._remote_finished()
            elif frame_type == RESET:
                body = _read_control(payload)
                reason = StreamReset(
                    str(body.get("code") or ResetCode.CANCELLED),
                    str(body.get("message") or ""),
                )
                waiter = self._opening.get(stream_id)
                if waiter is not None and not waiter.done():
                    waiter.set_exception(reason)
                stream._terminate(reason)
        except StreamReset as reset:
            await stream.reset(reset.code, reset.message)
        except MuxError as error:
            await stream.reset(ResetCode.PROTOCOL, str(error))

    def _accepted(self, stream_id: int, payload: bytes) -> None:
        waiter = self._opening.get(stream_id)
        if waiter is None or waiter.done():
            raise MuxError("accept for a stream that is not opening")
        waiter.set_result(_window(_read_control(payload).get("window")))

    async def _opened_by_peer(self, stream_id: int, payload: bytes) -> None:
        if stream_id in self._streams or stream_id % 2 == self._next_id % 2:
            raise MuxError(f"the peer opened stream {stream_id}, not its to open")
        try:
            body = _read_control(payload)
            credit = _window(body.get("window"))
            target = body.get("target")
            if not isinstance(target, dict):
                raise MuxError("open names no target")
        except MuxError as error:
            await self._refuse(stream_id, ResetCode.PROTOCOL, str(error))
            return
        if self._accept is None:
            await self._refuse(stream_id, ResetCode.REFUSED, "this side opens nothing")
            return
        if len(self._streams) >= self._max_streams:
            await self._refuse(
                stream_id, ResetCode.TOO_MANY_STREAMS, "too many open streams"
            )
            return
        stream = MuxStream(self, stream_id, target=target, window=self._window)
        stream._granted(credit)
        self._streams[stream_id] = stream
        task = asyncio.ensure_future(self._serve(stream, target))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def accept(self, stream: MuxStream) -> None:
        """Tell the peer its stream is open. Called by the ``accept`` handler
        once its target is ready -- after its local connect succeeded."""
        await self.send_frame(
            ACCEPT, stream.stream_id, _control({"window": self._window})
        )

    async def _serve(self, stream: MuxStream, target: dict[str, object]) -> None:
        assert self._accept is not None
        try:
            await self._accept(stream, target)
        except StreamReset as reset:
            await stream.reset(reset.code, reset.message)
        except asyncio.CancelledError:
            await asyncio.shield(stream.reset(ResetCode.LINK_LOST, "closing"))
            raise
        except Exception as error:
            await stream.reset(ResetCode.INTERNAL, type(error).__name__)
        else:
            await stream.aclose()

    async def _refuse(self, stream_id: int, code: str, message: str) -> None:
        await self.send_frame(
            RESET, stream_id, _control({"code": code, "message": message})
        )

    # ---------------------------------------------------------------- closing

    def _forget(self, stream_id: int) -> None:
        self._streams.pop(stream_id, None)

    async def close(
        self, code: str = ResetCode.LINK_LOST, message: str = "the link closed"
    ) -> None:
        """The link is gone: every stream on it ends, and nothing more opens."""
        if self._closed is not None:
            return
        self._closed = StreamReset(code, message)
        for waiter in self._opening.values():
            if not waiter.done():
                waiter.set_exception(self._closed)
        for stream in list(self._streams.values()):
            stream._terminate(self._closed)
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def splice(stream: MuxStream, reader, writer) -> None:
    """Copy bytes both ways between a stream and an asyncio stream pair until
    both directions finish; used by the machine side to reach a local port."""

    async def outbound() -> None:
        while chunk := await reader.read(MAX_DATA_BYTES):
            await stream.write(chunk)
        await stream.write_eof()

    async def inbound() -> None:
        while chunk := await stream.read(MAX_DATA_BYTES):
            writer.write(chunk)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()

    tasks = [asyncio.ensure_future(outbound()), asyncio.ensure_future(inbound())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            if task.exception() is not None:
                raise task.exception()  # type: ignore[misc]
        await asyncio.gather(*pending)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        writer.close()
