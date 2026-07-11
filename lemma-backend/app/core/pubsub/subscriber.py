import asyncio
import json
from typing import AsyncIterator

from faststream.redis import RedisBroker
from faststream.redis.parser import BinaryMessageFormatV1

from app.core.config import settings
from app.core.log.log import get_logger

logger = get_logger(__name__)


class RedisStreamReader:
    """Programmatic Redis Stream tailer for resumable client-facing feeds.

    This is not a domain-event consumer and does not replace the centralized
    message bus/inbox subscriber policy. Datastore WebSocket clients use it only
    to replay and tail the already-published durable stream.
    """

    def __init__(self, channel_or_stream: str):
        """
        Initialize subscriber.

        Args:
            channel_or_stream: Channel name for pub/sub or stream name for Redis streams
        """
        self.channel_or_stream = channel_or_stream
        self.broker = RedisBroker(settings.redis_url)

    async def __aenter__(self):
        await self.broker.start()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.broker.stop()

    async def current_last_id(self) -> str:
        """Return the stream's current last id (``"0-0"`` if it has no entries).

        Resolving this before tailing lets a caller anchor a resumable read at
        "now" deterministically — every entry added afterwards is read — instead
        of relying on ``"$"`` being evaluated at the first blocking ``xread``.
        """
        redis = self.broker._connection
        try:
            info = await redis.xinfo_stream(self.channel_or_stream)
        except Exception:
            # Stream does not exist yet: there is no history, so reading from the
            # very beginning is equivalent to reading only new entries.
            return "0-0"
        last = info.get("last-generated-id") or info.get(b"last-generated-id")
        if last is None:
            return "0-0"
        return last.decode() if isinstance(last, bytes) else str(last)

    async def subscribe(self, start_id: str = "$") -> AsyncIterator[dict]:
        """
        Subscribe to a Redis Stream and yield messages (for permanent events).

        Uses FastStream's broker's underlying Redis connection to read streams.
        FastStream's stream subscriber is decorator-based, so we use the broker's
        Redis connection directly for programmatic access.

        Args:
            start_id: Redis stream id to read after. Defaults to ``"$"`` (only
                messages published after subscribing). Pass a concrete id (e.g.
                the last id a client saw) to resume and replay missed entries.
        """
        last_id = start_id

        try:
            # Access the broker's underlying Redis connection
            redis = self.broker._connection
            logger.info(f"Subscribed to stream: {self.channel_or_stream}")

            while True:
                # Block for 1 second looking for new messages
                streams = await redis.xread(
                    {self.channel_or_stream: last_id}, count=1, block=1000
                )

                if not streams:
                    continue

                for stream_name, messages in streams:
                    for message_id, data in messages:
                        last_id = message_id
                        stream_id = (
                            message_id.decode()
                            if isinstance(message_id, bytes)
                            else str(message_id)
                        )
                        try:
                            # FastStream stores messages in binary format with __data__ field
                            if b"__data__" in data:
                                # Parse FastStream's binary message format
                                binary_data = data[b"__data__"]
                                try:
                                    # Use FastStream's parser to decode the binary format
                                    decoded_message, headers = (
                                        BinaryMessageFormatV1.parse(binary_data)
                                    )

                                    # decoded_message may be bytes/str (JSON) or
                                    # already a dict. Decode bytes first so the
                                    # JSON body is unpacked to top-level fields
                                    # rather than wrapped under a "data" key.
                                    if isinstance(decoded_message, (bytes, bytearray)):
                                        decoded_message = decoded_message.decode(
                                            "utf-8", errors="ignore"
                                        )
                                    if isinstance(decoded_message, str):
                                        try:
                                            decoded_message = json.loads(
                                                decoded_message
                                            )
                                        except json.JSONDecodeError:
                                            pass  # Keep as string if not JSON

                                    payload = (
                                        decoded_message
                                        if isinstance(decoded_message, dict)
                                        else {"data": decoded_message}
                                    )
                                    payload["_stream_id"] = stream_id
                                    yield payload
                                except Exception as parse_error:
                                    logger.error(
                                        f"Error parsing FastStream binary message {message_id}: {parse_error}",
                                        exc_info=True,
                                    )
                                    # Fallback: try to decode as UTF-8 if possible
                                    try:
                                        fallback_data = {
                                            k.decode("utf-8", errors="ignore"): (
                                                v.decode("utf-8", errors="ignore")
                                                if isinstance(v, bytes)
                                                else v
                                            )
                                            for k, v in data.items()
                                        }
                                        fallback_data["_stream_id"] = stream_id
                                        yield fallback_data
                                    except Exception:
                                        logger.error(
                                            f"Failed to decode message {message_id} even with fallback"
                                        )
                            else:
                                # Fallback for non-FastStream format messages
                                decoded_data = {
                                    k.decode("utf-8", errors="ignore"): (
                                        v.decode("utf-8", errors="ignore")
                                        if isinstance(v, bytes)
                                        else v
                                    )
                                    for k, v in data.items()
                                }
                                decoded_data["_stream_id"] = stream_id
                                yield decoded_data
                        except Exception as e:
                            logger.error(
                                f"Error decoding message {message_id}: {e}",
                                exc_info=True,
                            )

                await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            logger.info(f"Stream subscription cancelled for: {self.channel_or_stream}")
        except Exception as e:
            logger.error(f"Error in stream subscription: {e}", exc_info=True)
            raise

    async def subscribe_channel(self) -> AsyncIterator[dict]:
        """
        Subscribe to a Redis Pub/Sub channel and yield messages (for realtime updates).

        Uses FastStream's broker to subscribe to Redis channels.
        """
        try:
            async with self.broker.subscriber(self.channel_or_stream) as subscriber:
                logger.info(f"Subscribed to channel: {self.channel_or_stream}")
                async for message in subscriber:
                    try:
                        # FastStream handles deserialization
                        if isinstance(message, (str, bytes)):
                            decoded_data = (
                                json.loads(message)
                                if isinstance(message, bytes)
                                else json.loads(message)
                            )
                        elif hasattr(message, "model_dump"):
                            # Pydantic model
                            decoded_data = message.model_dump()
                        elif isinstance(message, dict):
                            decoded_data = message
                        else:
                            # Try to convert to dict
                            decoded_data = (
                                dict(message)
                                if hasattr(message, "__dict__")
                                else {"data": message}
                            )

                        yield decoded_data
                    except Exception as e:
                        logger.error(
                            f"Error processing channel message: {e}", exc_info=True
                        )

        except Exception as e:
            logger.error(f"Error in channel subscription: {e}", exc_info=True)
            raise
