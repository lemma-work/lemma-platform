"""Exact monetary values and immutable provider request receipts."""

from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.usage.domain.errors import UsageDomainError


MONEY_QUANTUM = Decimal("0.000000001")


def money(value: Decimal | int | str | float) -> Decimal:
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Usage money must be finite and nonnegative")
    return amount.quantize(MONEY_QUANTUM, rounding=ROUND_CEILING)


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


class BudgetWindow(BaseModel):
    model_config = ConfigDict(frozen=True)

    organization_id: UUID | None
    user_id: UUID | None
    kind: str
    start: datetime
    end: datetime
    limit: Decimal | None = Field(default=None, ge=0)
    excluded_organization_ids: tuple[UUID, ...] = ()


class RequestReceipt(BaseModel):
    """One immutable provider outcome, replayable after a lost commit response."""

    model_config = ConfigDict(frozen=True)

    request_id: UUID
    counts: TokenCounts
    cost: Decimal | None = Field(default=None, ge=0)
    occurred_at: datetime


class AccountingConflictError(UsageDomainError):
    """A request receipt was replayed with conflicting content."""

    def __init__(self, reason: str) -> None:
        super().__init__(
            "Usage accounting could not safely authorize or settle this request. Please contact your workspace administrator.",
            code="USAGE_ACCOUNTING_CONFLICT",
            status_code=409,
        )
        self.details = {"reason": reason}
