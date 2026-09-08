"""One committed journal entry and one settlement per provider dispatch."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from app.core.domain.events import DomainEvent
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.usage.config import UsageSettings
from app.modules.usage.domain.accounting import (
    BudgetWindow,
    MeteringIdentity,
    RequestReceipt,
)
from app.modules.usage.domain.budget_windows import budget_windows
from app.modules.usage.domain.errors import UsageLimitExceededError
from app.modules.usage.domain.events import ModelUsageEvent
from app.modules.usage.services.usage_service import UsageService
from app.modules.usage.domain.ports import UsageLimitValues, normalize_limit_values
from app.modules.usage.infrastructure import request_accounting
from app.modules.usage.infrastructure.price_catalog import RateCard
from app.modules.usage.services.usage_limit_provider import build_usage_limit_port
from app.core.log.log import get_logger


logger = get_logger(__name__)


class PostgresRequestAccountingGateway:
    def __init__(
        self,
        factory: UnitOfWorkFactory,
        identity: MeteringIdentity,
        pricing: RateCard,
        settings: UsageSettings,
    ) -> None:
        self.factory, self.identity, self.pricing, self.settings = (
            factory,
            identity,
            pricing,
            settings,
        )

    async def _limits(self, uow: SqlAlchemyUnitOfWork) -> UsageLimitValues:
        provider = build_usage_limit_port(uow)
        return (
            normalize_limit_values(
                await provider.resolve_limits(
                    organization_id=self.identity.organization_id,
                    user_id=self.identity.user_id,
                )
            )
            if provider is not None
            else UsageLimitValues()
        )

    def _windows(self, limits: UsageLimitValues, now: datetime) -> list[BudgetWindow]:
        return [
            window
            for window in budget_windows(self.identity, limits, now)
            if window.limit is not None
        ]

    async def begin(
        self,
        request_id: UUID,
        now: datetime,
        *,
        priceable: bool = True,
        in_flight: bool = False,
    ) -> bool:
        """Admit one request, and say whether a monetary limit applies to it.

        Under a monetary limit a request that cannot be priced cannot be
        enforced, so it is refused. `in_flight` is what stops that refusal
        landing in the wrong place. Whether a request is priceable depends on
        the shape of the messages, and the shape changes mid-run: the first
        request of a run is an ordinary prompt and priceable, and the
        continuation carrying a tool's non-JSON result is not. Refusing there
        ended the run partway through, after the tokens for the earlier
        requests had already been spent and billed, and presented it as a limit
        the account had hit.

        A run that is already under way is therefore admitted. Its request is
        recorded unpriced, which is what `metered_model` turns into
        `require_reconciliation` -- so the run is stopped at the next request
        boundary rather than in the middle of answering, and the spend that
        could not be priced is still visible in the ledger.
        """
        async with self.factory() as uow:
            windows = self._windows(await self._limits(uow), now)
            limited = bool(windows)
            if (
                limited
                and (not priceable or not self.pricing.priceable)
                and not in_flight
            ):
                # Two very different deployments produce this one refusal, and
                # the message cannot tell them apart: a request whose message
                # shape has no price, or a model whose rate card is not
                # enforceable at all. The second is a deployment that has to
                # state its own prices -- an OpenAI-compatible gateway reselling
                # somebody else's models resolves a price for the *vendor* and
                # is correctly refused the right to enforce a budget with it --
                # and nothing said so, so it read as a bug in the request.
                logger.warning(
                    "usage.request_accounting_gateway.request_not_priceable.degraded",
                    model=self.pricing.model,
                    provider=self.pricing.provider,
                    request_shape_priceable=priceable,
                    rate_card_enforceable=self.pricing.enforceable,
                )
                raise UsageLimitExceededError(
                    "This request needs supported usage reporting and a known price to run with monetary limits",
                    reason="configuration",
                )
            await request_accounting.begin(
                uow.session, request_id, self.identity, self.pricing, windows, now
            )
            return limited

    async def record(
        self, receipt: RequestReceipt, now: datetime | None = None
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        async with self.factory() as uow:
            limits = await self._limits(uow)
            events: list[DomainEvent] = []
            exhausted = await request_accounting.record(
                uow.session,
                receipt,
                self.identity,
                self._windows(limits, receipt.occurred_at),
                self._windows(limits, now),
                events,
                Decimal(str(self.settings.usage_limit_warn_fraction)),
            )
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
        return exhausted
