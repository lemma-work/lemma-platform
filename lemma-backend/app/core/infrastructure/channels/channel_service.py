"""Process-wide Redis Pub/Sub multiplexer for transient realtime channels."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from opentelemetry import metrics
from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError, TimeoutError as RedisTimeoutError

from app.core.config import settings
from app.core.domain.realtime import RealtimeChannel, RealtimeSlowConsumerError
from app.core.log.log import get_logger

logger = get_logger(__name__)
meter = metrics.get_meter(__name__)
subscriber_delta = meter.create_up_down_counter("lemma.realtime.subscribers")
reconnect_counter = meter.create_counter("lemma.realtime.reconnects")
slow_client_counter = meter.create_counter("lemma.realtime.slow_client_drops")

CLIENT_QUEUE_FRAMES = 256
RECONNECT_MAX_SECONDS = 10.0


@dataclass(eq=False, slots=True)
class _ClientSubscription:
    channels: tuple[str, ...]
    queue: asyncio.Queue[str | bytes | BaseException] = field(
        default_factory=lambda: asyncio.Queue(maxsize=CLIENT_QUEUE_FRAMES)
    )
    closed: bool = False


class RedisChannelAdapter:
    """One reconnecting Pub/Sub listener fan-outs into bounded client queues."""

    def __init__(self, redis_url: str | None = None, *, client: Redis | None = None):
        self.redis_url = redis_url
        self._redis = client
        self._owns_client = client is None
        self._pubsub: PubSub | None = None
        self._listener_task: asyncio.Task[None] | None = None
        self._clients_by_channel: dict[str, set[_ClientSubscription]] = {}
        self._connect_lock = asyncio.Lock()
        self._subscription_lock = asyncio.Lock()
        self._closing = False

    async def connect(self) -> None:
        """Create the shared Redis pool; the Pub/Sub lease stays lazy."""
        if self._redis is not None:
            return
        async with self._connect_lock:
            if self._redis is None:
                self._redis = Redis.from_url(
                    self.redis_url or settings.redis_url,
                    decode_responses=True,
                    health_check_interval=30,
                    socket_keepalive=True,
                    max_connections=settings.redis_max_connections,
                )
                self._owns_client = True

    async def disconnect(self) -> None:
        """Stop the one listener, close its lease, then close the owned pool."""
        self._closing = True
        task = self._listener_task
        self._listener_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        clients = {
            client
            for channel_clients in self._clients_by_channel.values()
            for client in channel_clients
        }
        self._clients_by_channel.clear()
        for client in clients:
            self._close_client(client, RuntimeError("Realtime service stopped"))

        await self._close_pubsub()
        redis_client = self._redis
        self._redis = None
        if redis_client is not None and self._owns_client:
            await redis_client.aclose()
        self._closing = False

    async def _client(self) -> Redis:
        await self.connect()
        assert self._redis is not None
        return self._redis

    async def publish(self, channel: str, message: object) -> None:
        """Publish a transient payload through the shared Redis pool."""
        payload: str | bytes
        if isinstance(message, (str, bytes)):
            payload = message
        else:
            payload = json.dumps(message, default=str)
        await (await self._client()).publish(channel, payload)

    @asynccontextmanager
    async def subscribe(
        self, channels: Sequence[str]
    ) -> AsyncGenerator[AsyncIterator[str | bytes], None]:
        """Register a bounded logical subscription on the shared listener."""
        normalized = tuple(dict.fromkeys(channels))
        if not normalized:
            raise ValueError("At least one realtime channel is required")

        client = _ClientSubscription(channels=normalized)
        await self._register(client)

        async def iterator() -> AsyncIterator[str | bytes]:
            while True:
                item = await client.queue.get()
                if isinstance(item, BaseException):
                    raise item
                yield item

        try:
            yield iterator()
        finally:
            await self._unregister(client)

    async def _register(self, client: _ClientSubscription) -> None:
        async with self._subscription_lock:
            pubsub = await self._ensure_pubsub()
            new_channels = [
                channel
                for channel in client.channels
                if channel not in self._clients_by_channel
            ]
            if new_channels:
                try:
                    await pubsub.subscribe(*new_channels)
                except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
                    reconnect_counter.add(1)
                    logger.warning(
                        "Realtime Pub/Sub subscribe failed; replacing stale lease",
                        error_type=type(exc).__name__,
                    )
                    await self._replace_pubsub()
                    pubsub = await self._ensure_pubsub()
                    recovery_channels = tuple(
                        dict.fromkeys((*self._clients_by_channel, *new_channels))
                    )
                    try:
                        await pubsub.subscribe(*recovery_channels)
                    except RedisConnectionError, RedisTimeoutError, OSError:
                        # Existing logical subscribers must retain the reconnecting
                        # listener even when the one inline retry also fails.
                        if self._clients_by_channel:
                            self._ensure_listener()
                        raise
            for channel in client.channels:
                self._clients_by_channel.setdefault(channel, set()).add(client)
            subscriber_delta.add(1)
            self._ensure_listener()

    async def _replace_pubsub(self) -> None:
        """Stop the listener and discard a stale Pub/Sub connection lease."""
        task = self._listener_task
        self._listener_task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._close_pubsub()

    async def _unregister(self, client: _ClientSubscription) -> None:
        if client.closed:
            return
        client.closed = True
        async with self._subscription_lock:
            empty_channels: list[str] = []
            for channel in client.channels:
                clients = self._clients_by_channel.get(channel)
                if clients is None:
                    continue
                clients.discard(client)
                if not clients:
                    self._clients_by_channel.pop(channel, None)
                    empty_channels.append(channel)
            pubsub = self._pubsub
            if empty_channels and pubsub is not None:
                await pubsub.unsubscribe(*empty_channels)
        subscriber_delta.add(-1)

    async def _ensure_pubsub(self) -> PubSub:
        if self._pubsub is None:
            self._pubsub = (await self._client()).pubsub(ignore_subscribe_messages=True)
        return self._pubsub

    def _ensure_listener(self) -> None:
        if self._listener_task is None or self._listener_task.done():
            self._listener_task = asyncio.create_task(
                self._listen(),
                name="redis-pubsub-multiplexer",
            )

    async def _listen(self) -> None:
        backoff = 0.25
        while not self._closing:
            try:
                pubsub = await self._ensure_pubsub()
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    data = message.get("data")
                    if not isinstance(data, (str, bytes)):
                        continue
                    raw_channel = message.get("channel")
                    channel = (
                        raw_channel.decode()
                        if isinstance(raw_channel, bytes)
                        else raw_channel
                    )
                    await self._fan_out(channel, data)
                return
            except asyncio.CancelledError:
                raise
            except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
                if self._closing:
                    return
                reconnect_counter.add(1)
                logger.warning(
                    "Realtime Pub/Sub connection lost; reconnecting",
                    error_type=type(exc).__name__,
                )
                await self._reconnect(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_SECONDS)

    async def _fan_out(self, channel: object, data: str | bytes) -> None:
        async with self._subscription_lock:
            if isinstance(channel, str):
                clients = tuple(self._clients_by_channel.get(channel, ()))
            else:
                # Compatibility with small test doubles that omit the channel.
                clients = tuple(
                    {
                        client
                        for channel_clients in self._clients_by_channel.values()
                        for client in channel_clients
                    }
                )
        for client in clients:
            if client.closed:
                continue
            try:
                client.queue.put_nowait(data)
            except asyncio.QueueFull:
                slow_client_counter.add(1)
                await self._evict_slow_client(client)

    async def _evict_slow_client(self, client: _ClientSubscription) -> None:
        while not client.queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                client.queue.get_nowait()
        self._close_client(client, RealtimeSlowConsumerError())
        await self._unregister(client)

    @staticmethod
    def _close_client(
        client: _ClientSubscription,
        reason: BaseException,
    ) -> None:
        if client.queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                client.queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            client.queue.put_nowait(reason)

    async def _reconnect(self, delay: float) -> None:
        await self._close_pubsub()
        await asyncio.sleep(delay)
        async with self._subscription_lock:
            channels = tuple(self._clients_by_channel)
            if not channels:
                return
            pubsub = await self._ensure_pubsub()
            await pubsub.subscribe(*channels)

    async def _close_pubsub(self) -> None:
        pubsub = self._pubsub
        self._pubsub = None
        if pubsub is None:
            return
        try:
            await pubsub.aclose()
        except RedisError, OSError:
            logger.warning("Failed to close realtime Pub/Sub connection")


channel_service = RedisChannelAdapter()


async def get_channel_service() -> RealtimeChannel:
    """FastAPI dependency returning the process-wide realtime channel port."""
    await channel_service.connect()
    return channel_service
