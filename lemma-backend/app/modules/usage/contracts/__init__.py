"""Public usage DTOs consumed by model runtimes."""

from typing import NamedTuple, Protocol

from pydantic import BaseModel

from app.modules.usage.domain.entities import UsageReservation
from app.modules.usage.domain.errors import (
    UsageContextMissingError,
    UsageLimitExceededError,
)


class ModelPricing(NamedTuple):
    input_per_million_usd: float
    output_per_million_usd: float
    unit_usd: float = 0.0
    cached_input_per_million_usd: float | None = None
    cache_write_per_million_usd: float | None = None


class AgentRunUsage(BaseModel):
    """Normalized billable usage produced by an agent/model runtime."""

    model_name: str
    usage_kind: str = "llm"
    input_tokens: int = 0
    output_tokens: int = 0
    units: float = 0.0
    request_count: int = 0
    tool_call_count: int = 0
    metadata: dict[str, object] | None = None


class MeteredRequest(Protocol):
    """One paid non-model request being metered; see `metering.metered_request`."""

    def settle(self, *, input_tokens: int, output_tokens: int = 0) -> None:
        """The provider answered and reported this usage."""
        ...

    def reject(self) -> None:
        """The provider refused the request outright, so it cost nothing."""
        ...


__all__ = [
    "AgentRunUsage",
    "MeteredRequest",
    "ModelPricing",
    "UsageContextMissingError",
    "UsageLimitExceededError",
    "UsageReservation",
]
