"""An execution owns its batch meters, including their timer tasks."""

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import uuid4

import anyio

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.modules.usage.config import UsageSettings, usage_settings
from app.modules.usage.domain.accounting import MeteringIdentity
from app.modules.usage.infrastructure.price_catalog import RateCard, resolve_rate_card
from app.modules.usage.services.accounting_gateway import PostgresAccountingGateway
from app.modules.usage.services.batch_meter import BatchMeter
from app.modules.usage.services.usage_context import (
    UsageExecutionContext,
    usage_execution_context,
)
from app.modules.usage.services.usage_service import UsageService


class MeteringScope:
    def __init__(
        self,
        context: UsageExecutionContext,
        factory: UnitOfWorkFactory,
        settings: UsageSettings,
    ) -> None:
        self.context = context
        self.factory = factory
        self.settings = settings
        self.execution_id = uuid4()
        self.meters: dict[str, tuple[BatchMeter, RateCard]] = {}

    def meter(
        self, profile: Mapping[str, object], source: str | None
    ) -> tuple[BatchMeter, RateCard]:
        identity = MeteringIdentity(
            execution_id=self.execution_id,
            user_id=self.context.user_id,
            organization_id=self.context.organization_id,
            pod_id=self.context.pod_id,
            agent_id=self.context.agent_id,
            conversation_id=self.context.conversation_id,
            agent_run_id=self.context.agent_run_id,
            parent_agent_run_id=self.context.parent_agent_run_id,
            source_type=source or self.context.source_type,
            source_id=self.context.source_id,
            profile_id=str(profile.get("profile_id") or "unknown"),
            profile_scope=str(profile.get("scope") or "ORGANIZATION"),
            model_name=str(
                profile.get("model_name")
                or profile.get("provider_model_name")
                or "unknown"
            ),
            provider_model_name=str(
                profile.get("provider_model_name")
                or profile.get("model_name")
                or "unknown"
            ),
        )
        key = identity.model_dump_json()
        if key not in self.meters:
            UsageService._load_environment_metadata()
            card = resolve_rate_card(
                profile, UsageService._SYSTEM_MODEL_PRICING, datetime.now(timezone.utc)
            )
            gateway = PostgresAccountingGateway(
                self.factory, identity, card, self.settings
            )
            self.meters[key] = (
                BatchMeter(
                    gateway,
                    request_interval=self.settings.usage_batch_requests,
                    seconds=self.settings.usage_batch_seconds,
                ),
                card,
            )
        return self.meters[key]

    async def close(self) -> None:
        # Every timer must be joined even if one meter cannot flush. Each failed
        # flush leaves a durable allocation for recovery rather than a refund.
        results = await asyncio.gather(
            *(meter.close() for meter, _ in self.meters.values()),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                raise result
        errors = [result for result in results if isinstance(result, Exception)]
        if errors:
            raise ExceptionGroup("Usage checkpoint failed", errors)


_scope: ContextVar[MeteringScope | None] = ContextVar(
    "usage_metering_scope", default=None
)


def current_metering_scope() -> MeteringScope | None:
    return _scope.get()


@asynccontextmanager
async def metering_execution(
    context: UsageExecutionContext,
    *,
    factory: UnitOfWorkFactory | None = None,
    settings: UsageSettings | None = None,
) -> AsyncIterator[MeteringScope]:
    scope = MeteringScope(
        context,
        factory or SessionUnitOfWorkFactory(async_session_maker),
        settings or usage_settings,
    )
    token = _scope.set(scope)
    try:
        with usage_execution_context(context):
            yield scope
    finally:
        try:
            with anyio.fail_after(10, shield=True):
                await scope.close()
        finally:
            _scope.reset(token)
