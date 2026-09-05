"""Model metering and execution lifetime, published by usage."""

from __future__ import annotations

from collections.abc import Mapping, AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.usage.services.usage_context import UsageExecutionContext

from pydantic_ai.models import Model

if TYPE_CHECKING:
    from app.modules.usage.config import UsageSettings
    from app.modules.usage.services.metering_scope import MeteringScope


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
