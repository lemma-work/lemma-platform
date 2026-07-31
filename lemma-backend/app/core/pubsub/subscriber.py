import json
from typing import Any, AsyncIterator

from faststream.redis.parser import BinaryMessageFormatV1

from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger

logger = get_logger(__name__)


class RedisStreamReader:
    """Programmatic Redis Stream tailer for resumable client-facing feeds.

    This is not a domain-event consumer and does not replace the centralized
    message bus/inbox subscriber policy. Datastore WebSocket clients use it only
    to replay and tail the already-published durable stream.

    Reads go through the shared Redis pool. This used to construct a whole
    ``RedisBroker`` per instance, so every WebSocket connection paid for a
    broker plus its own connection pool purely to issue ``XREAD``.

    Entries are published by the FastStream message bus, so they carry its
    binary envelope: they must be read as raw bytes -- hence the
    ``decode_responses=False`` pool -- and decoded with FastStream's parser.
    """

    def __init__(self, channel_or_stream: str):
        self.channel_or_stream = channel_or_stream

    async def __aenter__(self) -> "RedisStreamReader":
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        # Nothing to tear down: the client is shared and outlives this reader.
        return None

    async def current_last_id(self) -> str:
        """Return the stream's current last id (``"0-0"`` if it has no entries).

        Resolving this before tailing lets a caller anchor a resumable read at
        "now" deterministically -- every entry added afterwards is read --
        instead of relying on ``"$"`` being evaluated at the first blocking
        ``xread``.
        """
        redis = get_redis(decode_responses=False)
        try:
            info = await redis.xinfo_stream(self.channel_or_stream)
        except Exception:
            # Stream does not exist yet: there is no history, so reading from
            # the beginning is equivalent to reading only new entries.
            return "0-0"
        last = info.get("last-generated-id") or info.get(b"last-generated-id")
        if last is None:
            return "0-0"
        return last.decode() if isinstance(last, bytes) else str(last)

    async def subscribe(self, start_id: str = "$") -> AsyncIterator[dict]:
        """Tail the stream, yielding each entry after ``start_id``.

        Pass a concrete id (e.g. the last id a client saw) to resume and replay
        missed entries.
        """
        redis = get_redis(decode_responses=False)
        last_id: Any = start_id
        while True:
            streams = await redis.xread(
                {self.channel_or_stream: last_id}, count=64, block=1000
            )
            if not streams:
                continue
            for _stream_name, messages in streams:
                for message_id, data in messages:
                    last_id = message_id
                    payload = _decode_entry(data)
                    if payload is None:
                        logger.warning("pubsub.message.dropped")
                        continue
                    payload["_stream_id"] = (
                        message_id.decode()
                        if isinstance(message_id, bytes)
                        else str(message_id)
                    )
                    yield payload


def _decode_entry(data: dict[Any, Any]) -> dict | None:
    """Decode one stream entry, preferring FastStream's binary envelope."""
    binary = data.get(b"__data__") or data.get("__data__")
    if binary is not None:
        try:
            decoded, _headers = BinaryMessageFormatV1.parse(binary)
        except Exception:
            logger.debug("pubsub.message.binary_parse_failed", exc_info=True)
            return _decode_plain(data)
        if isinstance(decoded, (bytes, bytearray)):
            decoded = decoded.decode("utf-8", errors="ignore")
        if isinstance(decoded, str):
            try:
                decoded = json.loads(decoded)
            except json.JSONDecodeError:
                pass
        return decoded if isinstance(decoded, dict) else {"data": decoded}
    return _decode_plain(data)


def _decode_plain(data: dict[Any, Any]) -> dict | None:
    """Fallback for entries not written through the message bus."""
    try:
        return {
            (k.decode("utf-8", errors="ignore") if isinstance(k, bytes) else k): (
                v.decode("utf-8", errors="ignore") if isinstance(v, bytes) else v
            )
            for k, v in data.items()
        }
    except Exception:
        return None
