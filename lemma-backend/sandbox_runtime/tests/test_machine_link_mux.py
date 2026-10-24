"""The mux a connected machine's streams ride on.

Two ``Mux`` objects joined by a pair of queues stand in for the link: each
side's ``send`` puts a message on a queue and a pump feeds it to the other
side, in order, exactly as one WebSocket would. The queues are bounded, so a
pump that stops pumping is a link whose socket is full.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import struct
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from sandbox_runtime.machine_link import mux
from sandbox_runtime.machine_link.mux import (
    DATA,
    Mux,
    MuxError,
    MuxStream,
    ResetCode,
    StreamReset,
    encode,
)


pytestmark = pytest.mark.unit


class Wire:
    """One direction of the link: a bounded queue and the pump draining it."""

    def __init__(self, *, capacity: int = 64) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue(capacity)
        self.sent: list[bytes] = []
        self.flowing = asyncio.Event()
        self.flowing.set()

    async def send(self, frame: bytes) -> None:
        self.sent.append(frame)
        await self.queue.put(frame)

    async def pump(self, into: Mux) -> None:
        while True:
            await self.flowing.wait()
            frame = await self.queue.get()
            await into.feed(frame)


@asynccontextmanager
async def linked(
    accept=None, *, window: int = mux.DEFAULT_WINDOW_BYTES, max_streams: int = 64
) -> AsyncIterator[tuple[Mux, Mux, Wire, Wire]]:
    down, up = Wire(), Wire()
    opener = Mux(down.send, role="opener", window=window, max_streams=max_streams)
    acceptor = Mux(
        up.send, role="acceptor", accept=accept, window=window, max_streams=max_streams
    )
    pumps = [
        asyncio.ensure_future(down.pump(acceptor)),
        asyncio.ensure_future(up.pump(opener)),
    ]
    try:
        yield opener, acceptor, down, up
    finally:
        for pump in pumps:
            pump.cancel()
        await asyncio.gather(*pumps, return_exceptions=True)
        await opener.close()
        await acceptor.close()


async def echo(stream: MuxStream, target: dict[str, object]) -> None:
    await stream._mux.accept(stream)
    while chunk := await stream.read():
        await stream.write(chunk)
    await stream.write_eof()


async def read_all(stream: MuxStream) -> bytes:
    received = bytearray()
    while chunk := await stream.read():
        received.extend(chunk)
    return bytes(received)


async def test_a_stream_carries_bytes_both_ways_and_its_target() -> None:
    targets: list[dict[str, object]] = []

    async def accept(stream: MuxStream, target: dict[str, object]) -> None:
        targets.append(target)
        await echo(stream, target)

    async with linked(accept) as (opener, _, _, _):
        stream = await opener.open({"kind": "port", "port": 8080})
        await stream.write(b"hello")
        await stream.write_eof()
        assert await read_all(stream) == b"hello"
        await asyncio.wait_for(stream.wait_closed(), 1)
    assert targets == [{"kind": "port", "port": 8080}]
    assert opener.stream_count == 0


async def test_a_refused_target_fails_the_open_with_the_reason() -> None:
    async def accept(stream: MuxStream, target: dict[str, object]) -> None:
        raise StreamReset(ResetCode.REFUSED, "nothing listens on 9")

    async with linked(accept) as (opener, _, _, _):
        with pytest.raises(StreamReset) as refused:
            await opener.open({"kind": "port", "port": 9})
    assert refused.value.code == ResetCode.REFUSED
    assert "nothing listens" in refused.value.message


async def test_a_side_that_accepts_nothing_refuses_every_open() -> None:
    async with linked(None) as (opener, _, _, _):
        with pytest.raises(StreamReset) as refused:
            await opener.open({"kind": "port", "port": 1})
    assert refused.value.code == ResetCode.REFUSED


async def test_a_sender_stops_at_the_window_until_the_reader_reads() -> None:
    window = 16 * 1024
    held: asyncio.Queue[MuxStream] = asyncio.Queue()

    async def accept(stream: MuxStream, target: dict[str, object]) -> None:
        await stream._mux.accept(stream)
        await held.put(stream)
        await stream.wait_closed()

    async with linked(accept, window=window) as (opener, _, down, _):
        stream = await opener.open({"kind": "sink"})
        far = await held.get()
        payload = os.urandom(window * 4)
        writing = asyncio.ensure_future(stream.write(payload))
        await asyncio.sleep(0.05)
        # Nothing read on the far side: exactly one window is in flight.
        assert not writing.done()
        sent = sum(
            len(frame) - mux.HEADER_BYTES for frame in down.sent if frame[0] == DATA
        )
        assert sent == window
        received = bytearray()
        while len(received) < len(payload):
            received.extend(await far.read())
        await asyncio.wait_for(writing, 1)
        assert bytes(received) == payload


async def test_a_peer_that_sends_past_its_window_has_the_stream_reset() -> None:
    window = 1024
    held: asyncio.Queue[MuxStream] = asyncio.Queue()

    async def accept(stream: MuxStream, target: dict[str, object]) -> None:
        await stream._mux.accept(stream)
        await held.put(stream)
        await stream.wait_closed()

    async with linked(accept, window=window) as (opener, _, down, _):
        stream = await opener.open({"kind": "sink"})
        far = await held.get()
        # Bypass the stream's own accounting, as a broken peer would.
        await down.send(encode(DATA, stream.stream_id, b"x" * (window + 1)))
        with pytest.raises(StreamReset) as reset:
            await asyncio.wait_for(far.read(), 1)
        assert reset.value.code == ResetCode.FLOW_CONTROL
        with pytest.raises(StreamReset):
            await asyncio.wait_for(stream.read(), 1)


async def test_a_slow_stream_does_not_hold_up_another_on_the_same_link() -> None:
    window = 8 * 1024
    held: dict[object, MuxStream] = {}
    arrived = asyncio.Event()

    async def accept(stream: MuxStream, target: dict[str, object]) -> None:
        if target["kind"] == "sink":
            await stream._mux.accept(stream)
            held["sink"] = stream
            arrived.set()
            await stream.wait_closed()
        else:
            await echo(stream, target)

    async with linked(accept, window=window) as (opener, _, _, _):
        stalled = await opener.open({"kind": "sink"})
        await arrived.wait()
        # Fill the stalled stream's window and then some; nobody reads it.
        blocked = asyncio.ensure_future(stalled.write(b"z" * window * 3))
        lively = await opener.open({"kind": "echo"})
        await lively.write(b"still moving")
        await lively.write_eof()
        assert await asyncio.wait_for(read_all(lively), 1) == b"still moving"
        assert not blocked.done()
        blocked.cancel()


async def test_many_streams_interleave_without_mixing_their_bytes() -> None:
    async with linked(echo) as (opener, _, _, _):

        async def round_trip(index: int) -> None:
            stream = await opener.open({"kind": "echo", "n": index})
            payload = os.urandom(200_000 + index)
            writer = asyncio.ensure_future(stream.write(payload))
            received = bytearray()
            while len(received) < len(payload):
                received.extend(await stream.read())
            await writer
            await stream.write_eof()
            assert await stream.read() == b""
            assert hashlib.sha256(received).digest() == hashlib.sha256(payload).digest()

        await asyncio.wait_for(asyncio.gather(*(round_trip(i) for i in range(12))), 10)
    assert opener.stream_count == 0


async def test_a_full_socket_slows_every_writer_instead_of_buffering() -> None:
    # A window far larger than the socket's queue, so the socket is the limit.
    async with linked(echo, window=mux.MAX_WINDOW_BYTES) as (opener, _, down, _):
        stream = await opener.open({"kind": "echo"})
        down.flowing.clear()
        writing = asyncio.ensure_future(stream.write(b"y" * mux.MAX_DATA_BYTES * 100))
        await asyncio.sleep(0.05)
        # The bounded queue is the socket: full, and the writer waits on it.
        assert not writing.done()
        assert down.queue.full()
        writing.cancel()
        down.flowing.set()


async def test_losing_the_link_resets_every_stream_and_refuses_new_ones() -> None:
    async with linked(echo) as (opener, acceptor, _, _):
        first = await opener.open({"kind": "echo"})
        second = await opener.open({"kind": "echo"})
        reading = asyncio.ensure_future(first.read())
        await asyncio.sleep(0)
        await opener.close()
        for pending in (reading, second.read()):
            with pytest.raises(StreamReset) as reset:
                await asyncio.wait_for(pending, 1)
            assert reset.value.code == ResetCode.LINK_LOST
        with pytest.raises(StreamReset):
            await opener.open({"kind": "echo"})
        with pytest.raises(StreamReset):
            await second.write(b"after")


async def test_half_close_lets_the_other_side_answer() -> None:
    async def accept(stream: MuxStream, target: dict[str, object]) -> None:
        await stream._mux.accept(stream)
        request = await read_all(stream)
        await stream.write(b"you said " + request)
        await stream.write_eof()

    async with linked(accept) as (opener, _, _, _):
        stream = await opener.open({"kind": "rpc"})
        await stream.write(b"ping")
        await stream.write_eof()
        assert await read_all(stream) == b"you said ping"


async def test_streams_past_the_limit_are_refused() -> None:
    async def hold(stream: MuxStream, target: dict[str, object]) -> None:
        await stream._mux.accept(stream)
        await stream.wait_closed()

    async with linked(hold, max_streams=2) as (opener, _, _, _):
        await opener.open({"kind": "a"})
        await opener.open({"kind": "b"})
        with pytest.raises(StreamReset) as refused:
            await opener.open({"kind": "c"})
        assert refused.value.code == ResetCode.TOO_MANY_STREAMS


async def test_a_peer_opening_an_id_of_the_wrong_parity_breaks_the_link() -> None:
    async with linked(echo) as (opener, _, _, _):
        # The acceptor's ids are even; an odd OPEN arriving at the opener is
        # the peer using ids that are not its own.
        frame = encode(mux.OPEN, 1, b'{"target":{},"window":1024}')
        with pytest.raises(MuxError):
            await opener.feed(frame)


async def test_an_undecodable_frame_is_a_protocol_error() -> None:
    async with linked(echo) as (opener, _, _, _):
        with pytest.raises(MuxError):
            await opener.feed(b"\x01")
        with pytest.raises(MuxError):
            await opener.feed(struct.pack(">BI", 99, 1))


async def test_closing_a_stream_early_resets_the_far_side() -> None:
    held: asyncio.Queue[MuxStream] = asyncio.Queue()

    async def accept(stream: MuxStream, target: dict[str, object]) -> None:
        await stream._mux.accept(stream)
        await held.put(stream)
        await stream.wait_closed()

    async with linked(accept) as (opener, _, _, _):
        stream = await opener.open({"kind": "sink"})
        far = await held.get()
        await stream.aclose()
        with pytest.raises(StreamReset) as reset:
            await asyncio.wait_for(far.read(), 1)
        assert reset.value.code == ResetCode.CANCELLED


async def test_splice_carries_a_stream_to_a_local_port_and_back() -> None:
    async def serve_upper(reader, writer) -> None:
        data = await reader.read()
        writer.write(data.upper())
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve_upper, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def accept(stream: MuxStream, target: dict[str, object]) -> None:
        reader, writer = await asyncio.open_connection("127.0.0.1", target["port"])
        await stream._mux.accept(stream)
        await mux.splice(stream, reader, writer)

    try:
        async with linked(accept) as (opener, _, _, _):
            stream = await opener.open({"kind": "port", "port": port})
            await stream.write(b"over the link")
            await stream.write_eof()
            assert await asyncio.wait_for(read_all(stream), 2) == b"OVER THE LINK"
    finally:
        server.close()
        await server.wait_closed()
