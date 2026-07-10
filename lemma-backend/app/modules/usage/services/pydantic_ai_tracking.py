"""Helpers for tracking direct Pydantic AI model calls."""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.events.message_bus import get_message_bus
from app.modules.usage.domain.entities import UsageReservation
from app.modules.usage.services.usage_context import UsageExecutionContext
from app.modules.usage.services.usage_service import UsageService
from app.modules.usage.services.usage_service_factory import build_usage_service


async def reserve_usage_for_runtime(
    *,
    organization_id: UUID | None,
    user_id: UUID,
    runtime_profile: dict[str, object | None] | None,
) -> UsageReservation | None:
    if not isinstance(runtime_profile, dict):
        return None
    profile_id = runtime_profile.get("profile_id")
    profile_scope = runtime_profile.get("scope")
    model_name = runtime_profile.get("model_name")
    if not isinstance(profile_id, str) or not isinstance(profile_scope, str):
        return None
    if not isinstance(model_name, str):
        model_name = str(runtime_profile.get("provider_model_name") or "default")
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session, message_bus=get_message_bus())
        reservation = await _usage_service(uow).reserve_for_profile(
            organization_id=organization_id,
            user_id=user_id,
            profile_id=profile_id,
            profile_scope=profile_scope,
            model_name=model_name,
        )
        await uow.commit()
        return reservation


async def release_usage_reservation(reservation: UsageReservation | None) -> None:
    if reservation is None:
        return
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session, message_bus=get_message_bus())
        await _usage_service(uow).release_reservation(reservation)
        await uow.commit()


async def record_pydantic_ai_result_usage(
    *,
    ctx: UsageExecutionContext,
    runtime_profile: dict[str, object | None] | None,
    result: object | None,
    status: str,
    reservation: UsageReservation | None,
    metadata: dict[str, object] | None = None,
) -> None:
    if result is None:
        await release_usage_reservation(reservation)
        return
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session, message_bus=get_message_bus())
        await _usage_service(uow).record_pydantic_ai_result_usage(
            ctx=ctx,
            runtime_profile=runtime_profile,
            result=result,
            status=status,
            reservation=reservation,
            metadata=metadata,
        )
        await uow.commit()


def _usage_service(uow: SqlAlchemyUnitOfWork) -> UsageService:
    return build_usage_service(uow)
