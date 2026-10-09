"""Public usage DTOs consumed by model runtimes."""

from typing import NamedTuple, Protocol

from pydantic import BaseModel

from app.modules.usage.domain.accounting import CONTACT_RUN, OUTSIDER_RUN
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


def outside_audience_source(
    *, answers_outsider: bool, answers_contact: bool
) -> str | None:
    """The source a run answering somebody outside the organization is
    recorded under, or ``None`` for any other run.

    Recorded apart, so it spends the organization's budget and its contacts
    cap rather than the allowance of the member who looks after the
    conversation. Handed to ``UsageExecutionContext.outside_audience`` by work
    done for such a run under a source of its own.
    """
    if answers_contact:
        return CONTACT_RUN
    if answers_outsider:
        return OUTSIDER_RUN
    return None


def run_source_type(*, answers_outsider: bool, answers_contact: bool) -> str:
    """What an agent run's usage is recorded under."""
    return (
        outside_audience_source(
            answers_outsider=answers_outsider, answers_contact=answers_contact
        )
        or "agent_run"
    )


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
    "outside_audience_source",
    "run_source_type",
    "MeteredRequest",
    "ModelPricing",
    "UsageContextMissingError",
    "UsageLimitExceededError",
    "UsageReservation",
]
