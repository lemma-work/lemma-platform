"""Bind an execution's accounting to short units of work."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.domain.events import DomainEvent
from app.modules.usage.domain.events import ModelUsageEvent
from app.modules.usage.services.usage_service import UsageService
from app.modules.usage.config import UsageSettings
from app.modules.usage.domain.accounting import (
    Allocation,
    MeteringIdentity,
    PricingUnavailableError,
    UsageBatch,
)
from app.modules.usage.domain.budget_windows import budget_windows
from app.modules.usage.domain.ports import UsageLimitValues, normalize_limit_values
from app.modules.usage.domain.errors import UsageLimitExceededError
from app.modules.usage.infrastructure import allocation_repository
from app.modules.usage.infrastructure.price_catalog import RateCard
from app.modules.usage.services.usage_limit_provider import build_usage_limit_port


class PostgresAccountingGateway:
    def __init__(
        self,
        factory: UnitOfWorkFactory,
        identity: MeteringIdentity,
        pricing: RateCard,
        settings: UsageSettings,
    ) -> None:
        self.factory = factory
        self.identity = identity
        self.pricing = pricing
        self.settings = settings

    async def open(
        self, allocation_id: UUID, required: Decimal | None, now: datetime
    ) -> Allocation:
        async with self.factory() as uow:
            await allocation_repository.mark_expired_uncertain(uow.session, now)
            limits = await self._resolve_limits(uow)
            windows = budget_windows(self.identity, limits, now)
            if any(window.limit is not None for window in windows) and required is None:
                raise UsageLimitExceededError(
                    "This model needs a known provider price and request bound to run with monetary limits. Configure a pricing override or use a supported model."
                ) from PricingUnavailableError()
            return await allocation_repository.open_allocation(
                uow.session,
                allocation_id=allocation_id,
                identity=self.identity,
                pricing=self.pricing,
                windows=windows,
                required=required or Decimal(0),
                target=self.settings.usage_budget_chunk_usd,
                now=now,
                timeout_seconds=self.settings.usage_allocation_timeout_seconds,
            )

    async def checkpoint(self, batch: UsageBatch, now: datetime) -> Allocation:
        async with self.factory() as uow:
            events: list[DomainEvent] = []
            allocation = await allocation_repository.checkpoint(
                uow.session,
                batch,
                events=events,
                warning_fraction=Decimal(str(self.settings.usage_limit_warn_fraction)),
                now=now,
                timeout_seconds=self.settings.usage_allocation_timeout_seconds,
            )
            if not allocation.limited:
                limits = await self._resolve_limits(uow)
                if any(
                    window.limit is not None
                    for window in budget_windows(self.identity, limits, now)
                ):
                    allocation = allocation.model_copy(update={"expires_at": now})
            uow.collect_events(events)
        for event in events:
            if isinstance(event, ModelUsageEvent):
                UsageService._record_usage_metrics(
                    model_name=event.model_name,
                    usage_kind=event.usage_kind,
                    input_tokens=event.input_tokens,
                    output_tokens=event.output_tokens,
                    cost_usd=event.cost_usd,
                )
        return allocation

    async def _resolve_limits(self, uow: SqlAlchemyUnitOfWork) -> UsageLimitValues:
        provider = build_usage_limit_port(uow)
        if provider is None:
            return UsageLimitValues()
        return normalize_limit_values(
            await provider.resolve_limits(
                organization_id=self.identity.organization_id,
                user_id=self.identity.user_id,
            )
        )
