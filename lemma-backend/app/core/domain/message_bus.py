"""Message bus contracts for domain event publishing."""

from collections.abc import Mapping
from typing import Any, Protocol
from pydantic import BaseModel


class MessageBus(Protocol):
    """Port for publishing integration/domain events."""

    async def publish(
        self, stream: str, event: BaseModel | Mapping[str, Any]
    ) -> None:
        """Publish an event payload to a stream/topic."""
        ...
