"""Durable batch receipts and exclusive spending authority."""

from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


MONEY_QUANTUM = Decimal("0.000000001")


def money(value: Decimal | int | str | float) -> Decimal:
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Usage money must be finite and nonnegative")
    return amount.quantize(MONEY_QUANTUM, rounding=ROUND_CEILING)


class AllocationState(StrEnum):
    ACTIVE = "ACTIVE"
    UNCERTAIN = "UNCERTAIN"
    CLOSED = "CLOSED"


class CostSource(StrEnum):
    REGISTERED = "REGISTERED"
    ESTIMATED = "ESTIMATED"
    UNKNOWN = "UNKNOWN"
    LEGACY = "LEGACY"


class MeteringIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    execution_id: UUID
    organization_id: UUID | None = None
    user_id: UUID
    pod_id: UUID | None = None
    agent_id: UUID | None = None
    conversation_id: UUID | None = None
    agent_run_id: UUID | None = None
    parent_agent_run_id: UUID | None = None
    source_type: str = "agent_run"
    source_id: str | None = None
    profile_id: str
    profile_scope: str
    model_name: str
    provider_model_name: str


class TokenCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    input_audio_tokens: int = Field(default=0, ge=0)
    output_audio_tokens: int = Field(default=0, ge=0)
    cache_audio_read_tokens: int = Field(default=0, ge=0)
    unpriced_requests: int = Field(default=0, ge=0)
    unconfirmed_requests: int = Field(default=0, ge=0)
    request_count: int = Field(default=0, ge=0)

    def plus(self, other: "TokenCounts") -> "TokenCounts":
        return TokenCounts(
            **{
                name: getattr(self, name) + getattr(other, name)
                for name in type(self).model_fields
            }
        )


class BudgetWindow(BaseModel):
    model_config = ConfigDict(frozen=True)

    organization_id: UUID | None
    user_id: UUID | None
    kind: str
    start: datetime
    end: datetime
    limit: Decimal | None = Field(default=None, ge=0)
    excluded_organization_ids: tuple[UUID, ...] = ()


class Allocation(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    amount: Decimal
    limited: bool
    expires_at: datetime
    window_end: datetime


class UsageBatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    allocation_id: UUID
    sequence: int = Field(ge=1)
    counts: TokenCounts
    cost: Decimal | None = Field(default=None, ge=0)
    uncertain: Decimal = Field(default=Decimal(0), ge=0)
    occurred_at: datetime
    close: bool = False


class PricingUnavailableError(ValueError):
    """A limited execution has no enforceable rate or request bound."""


class AccountingConflictError(ValueError):
    """A receipt was changed or exceeds its exclusively allocated budget."""
