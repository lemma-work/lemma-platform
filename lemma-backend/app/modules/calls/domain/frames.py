"""JSON frames to and from voice providers, and the queues events wait in.

Provider sockets are opened with ``MAX_PROVIDER_FRAME_BYTES``, so a frame
decoded here is never bigger than that: an audio chunk or a transcript line.
Parsing one on the event loop costs less than handing it to a thread, and a
call sends dozens a second.
"""

from __future__ import annotations

import asyncio
import json

from pydantic import JsonValue

from app.core.log.log import get_logger

logger = get_logger(__name__)

#: The largest frame a provider may send; the socket refuses anything bigger.
MAX_PROVIDER_FRAME_BYTES = 1024 * 1024
#: Events waiting for the session. Audio is most of them, a few dozen a
#: second, so this is tens of seconds of backlog: a session that far behind
#: has already lost the call, and dropping beats growing without end.
EVENT_BACKLOG = 1024

type Frame = dict[str, JsonValue]


def decode(raw: str | bytes) -> Frame:
    """One provider frame; an empty dict for anything that is not an object."""
    try:
        frame = json.loads(raw)
    except ValueError:
        return {}
    return frame if isinstance(frame, dict) else {}


def encode(frame: Frame) -> str:
    return json.dumps(frame)


def offer[T](queue: asyncio.Queue[T], item: T, *, essential: bool = False) -> None:
    """Put ``item`` on a bounded queue, dropping it if the consumer has fallen
    that far behind.

    An ``essential`` item -- the event that ends a stream -- is never the one
    dropped: the oldest waiting item makes room for it, because a reader that
    never sees its stream end waits on it forever.
    """
    try:
        queue.put_nowait(item)
        return
    except asyncio.QueueFull:
        logger.warning("calls.frames.backlog_full.degraded", item=type(item).__name__)
    if essential:
        queue.get_nowait()
        queue.put_nowait(item)
