"""Usage reservation/recording/release for agent runs.

Extracted so the background runner and the inline sub-agent/function paths share
one implementation instead of duplicating reserve/record/release plumbing.
"""

from __future__ import annotations

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.composition.agent_usage import (
    UsageReservation,
    UsageService,
    build_usage_service,
)


class RunUsageRecorder:
    """Thin façade over `UsageService` for the agent run lifecycle."""

    def __init__(self, uow_factory: UnitOfWorkFactory):
        self.uow_factory = uow_factory

    def _service(self, uow) -> UsageService:
        return build_usage_service(uow)

    async def reserve(
        self,
        *,
        organization_id,
        user_id,
        runtime_profile: dict[str, object | None],
    ) -> UsageReservation | None:
        profile_id = runtime_profile.get("profile_id")
        profile_scope = runtime_profile.get("scope")
        model_name = runtime_profile.get("model_name")
        if not isinstance(profile_id, str) or not isinstance(profile_scope, str):
            return None
        if not isinstance(model_name, str):
            model_name = str(runtime_profile.get("provider_model_name") or "default")
        async with self.uow_factory() as uow:
            reservation = await self._service(uow).reserve_for_profile(
                organization_id=organization_id,
                user_id=user_id,
                profile_id=profile_id,
                profile_scope=profile_scope,
                model_name=model_name,
            )
            await uow.commit()
            return reservation

    async def release(self, reservation: UsageReservation | None) -> None:
        if reservation is None:
            return
        async with self.uow_factory() as uow:
            await self._service(uow).release_reservation(reservation)
            await uow.commit()

    async def record(
        self,
        *,
        ctx,
        runtime_profile: dict[str, object | None] | None,
        usage_data,
        status: str,
        reservation: UsageReservation | None,
    ) -> None:
        async with self.uow_factory() as uow:
            await self._service(uow).record_agent_run_usage(
                ctx=ctx,
                runtime_profile=runtime_profile,
                usage_data=usage_data,
                status=status,
                reservation=reservation,
            )
            await uow.commit()
