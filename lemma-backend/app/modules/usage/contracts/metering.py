"""Model metering and execution lifetime, published by usage."""

from __future__ import annotations

from collections.abc import Mapping, AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.usage.services.usage_context import UsageExecutionContext

from pydantic_ai.models import Model
from app.modules.usage.domain.errors import UsageLimitExceededError
from app.modules.usage.services.usage_service_factory import build_usage_service

if TYPE_CHECKING:
    from app.modules.usage.config import UsageSettings
    from app.modules.usage.services.metering_scope import MeteringScope


async def check_run_budget(
    *,
    factory: UnitOfWorkFactory,
    organization_id: UUID | None,
    user_id: UUID,
    profile_scope: str,
) -> None:
    """Refuse an exhausted run before setup, without reserving future spend."""
    if profile_scope.upper() != "SYSTEM":
        return
    async with factory() as uow:
        limits = await build_usage_service(uow).get_usage_limits(
            organization_id=organization_id, user_id=user_id
        )
    if any(
        scope["limit_usd"] is not None and scope["used_usd"] >= scope["limit_usd"]
        for scope in (
            limits["org_monthly"],
            limits["user_weekly"],
            limits["user_monthly"],
        )
    ):
        raise UsageLimitExceededError()


@asynccontextmanager
async def metering_execution(
    context: UsageExecutionContext,
    *,
    factory: UnitOfWorkFactory | None = None,
    settings: UsageSettings | None = None,
) -> AsyncIterator["MeteringScope"]:
    from app.modules.usage.services.metering_scope import metering_execution as execute

    async with execute(context, factory=factory, settings=settings) as scope:
        yield scope


def meter_model(
    model: Model, profile: Mapping[str, object], *, source: str | None = None
) -> Model:
    from app.modules.usage.infrastructure.metered_model import MeteredModel

    if isinstance(model, MeteredModel):
        return (
            model
            if source is None
            else MeteredModel(model.wrapped, model.runtime_profile, source=source)
        )
    return MeteredModel(model, profile, source=source)


async def finalize_metered_run(
    agent_run_id: UUID, status: str, *, factory: UnitOfWorkFactory
) -> None:
    """Drain a run's request receipts and retain its final outcome without rebilling."""
    from app.modules.usage.services.run_receipts import finalize_metered_run as finalize

    await finalize(agent_run_id, status, factory=factory)
