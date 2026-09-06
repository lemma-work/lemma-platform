"""Usage module ports."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, Sequence
from uuid import UUID

from app.modules.usage.domain.entities import UsageRecord, UsageSummary
from app.modules.usage.domain.query_types import UsageStatsBucket


class UsageRepositoryPort(Protocol):
    async def create(self, entity: UsageRecord) -> UsageRecord: ...

    async def list_usage(
        self,
        *,
        organization_id: UUID,
        start: datetime,
        end: datetime,
        pod_id: UUID | None = None,
        user_id: UUID | None = None,
        agent_id: UUID | None = None,
        agent_run_id: UUID | None = None,
        conversation_id: UUID | None = None,
        profile_id: str | None = None,
        profile_scope: str | None = None,
        model_name: str | None = None,
        usage_kind: str | None = None,
        source_type: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> Sequence[UsageRecord]: ...

    async def get_usage_summary(
        self,
        *,
        organization_id: UUID | None,
        start: datetime,
        end: datetime,
        pod_id: UUID | None = None,
        user_id: UUID | None = None,
        agent_id: UUID | None = None,
        agent_run_id: UUID | None = None,
        conversation_id: UUID | None = None,
        profile_id: str | None = None,
        profile_scope: str | None = None,
        model_name: str | None = None,
        usage_kind: str | None = None,
        source_type: str | None = None,
        status: str | None = None,
    ) -> UsageSummary: ...

    @abstractmethod
    async def get_usage_stats(
        self,
        *,
        organization_id: UUID,
        start: datetime,
        end: datetime,
        granularity: str = "day",
        group_by: str | None = None,
        pod_id: UUID | None = None,
        user_id: UUID | None = None,
        agent_id: UUID | None = None,
        agent_run_id: UUID | None = None,
        conversation_id: UUID | None = None,
        profile_id: str | None = None,
        profile_scope: str | None = None,
        model_name: str | None = None,
        usage_kind: str | None = None,
        source_type: str | None = None,
        status: str | None = None,
    ) -> Sequence[UsageStatsBucket]:
        """Return time buckets with optional dimension grouping."""


@dataclass(frozen=True)
class UsageLimitValues:
    """Resolved system-spend limits for an org/user context.

    ``None`` means unlimited for that window.
    """

    org_monthly_limit_usd: float | None = None
    user_weekly_limit_usd: float | None = None
    user_monthly_limit_usd: float | None = None
    user_limit_scope: Literal["organization", "global"] = "organization"
    excluded_organization_ids: tuple[UUID, ...] = ()


class UsageLimitPort(Protocol):
    """What usage needs from an external billing/plan provider: the spend limits
    that apply to an org+user. Implemented by the billing module (dependency
    inverts to billing -> usage); absent in builds without billing, where usage
    records metering data without monetary admission."""

    async def resolve_limits(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID,
    ) -> UsageLimitValues | None:
        """Return applicable spend limits; ``None`` means unlimited.

        The annotation said ``UsageLimitValues`` while this line said ``None``
        was allowed. No provider existed to return either, so nothing caught
        the disagreement -- see :func:`normalize_limit_values`.
        """
        ...


def normalize_limit_values(resolved: object) -> UsageLimitValues:
    """What a limit port returned, as ``UsageLimitValues``.

    Three shapes reach this. ``UsageLimitValues`` is the contract. ``None`` is
    the documented "unlimited" -- a provider may cover some organizations and
    not others. A two-tuple is an older adapter shape kept working here rather
    than in the service.

    It exists because the caller used to unpack whatever arrived as a tuple, so
    the documented ``None`` raised ``TypeError`` and 500'd every conversation
    start. That was unreachable while no provider was ever registered, which is
    exactly why it survived until one was.
    """
    if resolved is None:
        return UsageLimitValues()
    if isinstance(resolved, UsageLimitValues):
        return resolved
    org_monthly, user_weekly = resolved  # type: ignore[misc]
    return UsageLimitValues(
        org_monthly_limit_usd=org_monthly,
        user_weekly_limit_usd=user_weekly,
        user_monthly_limit_usd=None,
        user_limit_scope="organization",
    )
