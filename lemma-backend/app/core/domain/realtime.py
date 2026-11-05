"""Port for transient realtime channels used by SSE and websocket APIs."""

from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Protocol


class RealtimeSlowConsumerError(RuntimeError):
    """A bounded realtime subscriber failed to consume frames fast enough."""

    def __init__(self) -> None:
        super().__init__("Realtime client fell behind and was disconnected")


class RealtimeChannel(Protocol):
    """Publish and subscribe to ephemeral fan-out messages.

    Domain events use the durable Redis Streams message bus instead. Realtime
    channels intentionally provide no replay or delivery guarantee.
    """

    async def publish(self, channel: str, message: object) -> None:
        """Publish a transient message to all current subscribers."""
        ...

    async def subscriber_count(self, channel: str) -> int:
        """How many subscriptions ``channel`` has across every process, now.

        For a publisher whose message costs more to build than to send: with
        nobody listening it can skip the work. A subscriber that arrives just
        after a zero is not owed the message -- realtime carries no delivery
        guarantee, and its readers poll or snapshot on connect.
        """
        ...

    def subscribe(
        self, channels: Sequence[str]
    ) -> AbstractAsyncContextManager[AsyncIterator[str | bytes]]:
        """Lease a subscription until the returned context exits."""
        ...
