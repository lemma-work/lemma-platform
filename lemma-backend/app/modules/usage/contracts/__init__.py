"""Public usage DTOs consumed by model runtimes."""

from typing import NamedTuple

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


__all__ = [
    "AgentRunUsage",
    "ModelPricing",
    "UsageContextMissingError",
    "UsageLimitExceededError",
    "UsageReservation",
]
