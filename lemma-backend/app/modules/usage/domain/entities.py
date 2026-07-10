"""Usage domain entities."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.domain.entity import Entity


class UsageProfileScope(str, Enum):
    SYSTEM = "SYSTEM"
    ORGANIZATION = "ORGANIZATION"


class UsageKind(str, Enum):
    LLM = "LLM"
    IMAGE = "IMAGE"
    AUDIO = "AUDIO"
    EMBEDDING = "EMBEDDING"

    @classmethod
    def _missing_(cls, value: object) -> "UsageKind | None":
        if not isinstance(value, str):
            return None
        normalized = value.strip().upper()
        for member in cls:
            if member.value == normalized:
                return member
        return None


class UsageRecord(Entity):
    """One measured model usage item."""

    organization_id: UUID | None = None
    pod_id: UUID | None = None
    user_id: UUID
    agent_id: UUID | None = None
    conversation_id: UUID | None = None
    agent_run_id: UUID | None = None
    parent_agent_run_id: UUID | None = None
    source_type: str = "agent_run"
    source_id: str | None = None
    profile_id: str
    profile_scope: UsageProfileScope | str
    model_name: str
    usage_kind: UsageKind | str = UsageKind.LLM
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    units: float = Field(default=0.0, ge=0.0)
    cost_usd: float | None = Field(default=None, ge=0.0)
    status: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class UsageSummary(BaseModel):
    organization_id: UUID | None = None
    pod_id: UUID | None = None
    user_id: UUID | None = None
    agent_id: UUID | None = None
    start_date: datetime
    end_date: datetime
    period_days: int
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_units: float = 0.0
    system_cost_usd: float = 0.0
    total_by_profile: dict[str, dict[str, object]] = Field(default_factory=dict)
    total_by_model: dict[str, dict[str, object]] = Field(default_factory=dict)
    total_by_kind: dict[str, dict[str, object]] = Field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    def add_usage(self, record: UsageRecord) -> None:
        self.total_input_tokens += record.input_tokens
        self.total_output_tokens += record.output_tokens
        self.total_units += record.units
        if record.cost_usd is not None:
            self.system_cost_usd += record.cost_usd

        self._add_bucket(self.total_by_profile, record.profile_id, record)
        self._add_bucket(self.total_by_model, record.model_name, record)
        usage_kind = (
            record.usage_kind.value
            if isinstance(record.usage_kind, UsageKind)
            else record.usage_kind
        )
        self._add_bucket(self.total_by_kind, usage_kind, record)

    def _add_bucket(
        self,
        target: dict[str, dict[str, object]],
        key: str,
        record: UsageRecord,
    ) -> None:
        bucket = target.setdefault(
            key,
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "units": 0.0,
                "system_cost_usd": 0.0,
                "record_count": 0,
            },
        )
        bucket["input_tokens"] = _as_int(bucket["input_tokens"]) + record.input_tokens
        bucket["output_tokens"] = _as_int(bucket["output_tokens"]) + record.output_tokens
        bucket["total_tokens"] = _as_int(bucket["total_tokens"]) + record.total_tokens
        bucket["units"] = _as_float(bucket["units"]) + record.units
        bucket["record_count"] = _as_int(bucket["record_count"]) + 1
        if record.cost_usd is not None:
            bucket["system_cost_usd"] = (
                _as_float(bucket["system_cost_usd"]) + record.cost_usd
            )


def _as_int(value: object) -> int:
    if isinstance(value, (int, float, str)):
        return int(value)
    raise TypeError("Usage summary bucket value is not numeric")


def _as_float(value: object) -> float:
    if isinstance(value, (int, float, str)):
        return float(value)
    raise TypeError("Usage summary bucket value is not numeric")


class UsageReservation(BaseModel):
    """A monetary reservation created by an injected limit policy."""

    organization_id: UUID | None = None
    user_id: UUID
    amount_usd: float
    counter_ids: list[UUID] = Field(default_factory=list)


class UsageLimitCounterScope(BaseModel):
    """One atomically locked limit window participating in admission."""

    organization_id: UUID | None = None
    user_id: UUID | None = None
    window_kind: str
    window_start: datetime
    window_end: datetime
    limit_usd: float = Field(ge=0)
    initial_used_usd: float = Field(default=0.0, ge=0)
