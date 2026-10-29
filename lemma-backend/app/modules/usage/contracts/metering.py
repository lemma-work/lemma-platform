"""Model metering and execution lifetime, published by usage."""

from __future__ import annotations

from collections.abc import Mapping, AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.usage.contracts import MeteredRequest
from app.modules.usage.domain.accounting import (
    RUN_INDEX_METADATA_KEY as RUN_INDEX_METADATA_KEY,
)
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
    outside_audience: bool = False,
) -> None:
    """Refuse an exhausted run before setup, without reserving future spend.

    ``outside_audience``: the run answers a contact or a group's outsiders, so
    it is the organization's spend and the personal limits of ``user_id`` -- the
    member who looks after the conversation -- do not apply to it. The
    organization's contacts cap does, default included.

    Decided on the windows and running totals the run's first request will be
    admitted on. The check commits whatever a recount learned, refused or not,
    so that request does not count the ledger again.
    """
    if profile_scope.upper() != "SYSTEM":
        return
    async with factory() as uow:
        spent = await build_usage_service(uow).spent_windows(
            organization_id=organization_id,
            user_id=user_id,
            outside_audience=outside_audience,
        )
    if "contacts_month" in spent:
        raise UsageLimitExceededError(
            "The organization's monthly cap on answering contacts is reached"
        )
    if spent:
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
            else MeteredModel(
                model.wrapped,
                model.runtime_profile,
                source=source,
                retry_stream_connections=model.retry_stream_connections,
            )
        )
    return MeteredModel(model, profile, source=source)


@asynccontextmanager
async def metered_request(
    profile: Mapping[str, object], *, source: str | None = None
) -> AsyncIterator[MeteredRequest]:
    """Meter one paid request that is not a model call, inside `metering_execution`.

    `profile` names what is being paid for the way a runtime profile snapshot
    does (`profile_id`, `scope`, `model_name`), which is also what its price is
    looked up by. Before anything is sent the request is admitted against the
    execution's limits; call `settle` with what the provider reported, or
    `reject` when it refused, before the block ends.
    """
    from app.modules.usage.services.external_request import metered_external_request

    async with metered_external_request(profile, source=source) as outcome:
        yield outcome


def with_external_stream_retries(model: Model) -> Model:
    """Let a streaming caller recover connections while accounting each attempt."""
    from app.modules.usage.infrastructure.metered_model import MeteredModel

    if not isinstance(model, MeteredModel):
        return model
    return MeteredModel(
        model.wrapped,
        model.runtime_profile,
        source=model.source,
        retry_stream_connections=False,
    )


async def finalize_metered_run(
    agent_run_id: UUID, status: str, *, factory: UnitOfWorkFactory
) -> None:
    """Drain a run's request receipts and retain its final outcome without rebilling."""
    from app.modules.usage.services.run_receipts import finalize_metered_run as finalize

    await finalize(agent_run_id, status, factory=factory)
