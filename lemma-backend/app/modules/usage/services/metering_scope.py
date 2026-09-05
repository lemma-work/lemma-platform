"""Bind immediate request accounting and unfinished receipt writes to an execution."""

import asyncio
import sys
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
from app.modules.usage.domain.errors import UsageCheckpointError
from app.modules.usage.infrastructure.price_catalog import RateCard, resolve_rate_card
from app.modules.usage.services.request_accounting_gateway import (
    PostgresRequestAccountingGateway,
)
from app.modules.usage.services.request_meter import RequestMeter
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
        self.meters: dict[str, tuple[RequestMeter, RateCard]] = {}

    def meter(
        self, profile: Mapping[str, object], source: str | None
    ) -> tuple[RequestMeter, RateCard]:
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
            gateway = PostgresRequestAccountingGateway(
                self.factory, identity, card, self.settings
            )
            self.meters[key] = (
                RequestMeter(gateway),
                card,
            )
        return self.meters[key]

    async def close(self) -> None:
        # A lost commit response can be replayed using the same request identity.
        # No timer or database session outlives the execution.
        results = await asyncio.gather(
            *(meter.close() for meter, _ in self.meters.values()),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                raise result
        errors = [result for result in results if isinstance(result, Exception)]
        if errors:
            raise UsageCheckpointError() from ExceptionGroup(
                "Usage checkpoint failed", errors
            )


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
        failure = sys.exception()
        try:
            with anyio.fail_after(10, shield=True):
                await scope.close()
        except (UsageCheckpointError, TimeoutError) as close_error:
            if failure is not None:
                raise BaseExceptionGroup(
                    "Execution and usage finalization failed", [failure, close_error]
                ) from None
            raise
        finally:
            _scope.reset(token)
