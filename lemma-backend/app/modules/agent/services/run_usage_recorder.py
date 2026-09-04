"""Usage reservation/recording/release for agent runs.

Extracted so the background runner and the inline sub-agent/function paths share
one implementation instead of duplicating reserve/record/release plumbing.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.usage.contracts import UsageReservation
from app.modules.usage.contracts.execution import UsageService, build_usage_service
from app.modules.agent.services.run_phase_spans import run_phase


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
        agent_run_id: UUID | None = None,
    ) -> UsageReservation | None:
        profile_id = runtime_profile.get("profile_id")
        profile_scope = runtime_profile.get("scope")
        model_name = runtime_profile.get("model_name")
        if not isinstance(profile_id, str) or not isinstance(profile_scope, str):
            return None
        if not isinstance(model_name, str):
            model_name = str(runtime_profile.get("provider_model_name") or "default")
        with run_phase("usage_reserve"):
            async with self.uow_factory() as uow:
                reservation = await self._service(uow).reserve_for_profile(
                    organization_id=organization_id,
                    user_id=user_id,
                    profile_id=profile_id,
                    profile_scope=profile_scope,
                    model_name=model_name,
                )
                # Persisted in the same transaction that takes it, so the two can
                # never disagree: if the reservation committed, something other
                # than this worker's memory knows how to give it back.
                if reservation is not None and agent_run_id is not None:
                    await ConversationRepository(uow).store_usage_reservation(
                        agent_run_id=agent_run_id,
                        reservation={
                            "counter_ids": [
                                str(counter_id)
                                for counter_id in reservation.counter_ids
                            ],
                            "amount_usd": reservation.amount_usd,
                        },
                    )
                await uow.commit()
                return reservation

    async def release(
        self,
        reservation: UsageReservation | None,
        *,
        agent_run_id: UUID | None = None,
    ) -> None:
        if reservation is None:
            return
        async with self.uow_factory() as uow:
            if agent_run_id is not None:
                await ConversationRepository(uow).claim_usage_reservation(
                    agent_run_id=agent_run_id
                )
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
            # Clear the durable copy in the same transaction that consumes it,
            # so the reconciler never finds a handle for a run that already
            # settled up.
            if ctx.agent_run_id is not None:
                await ConversationRepository(uow).store_usage_reservation(
                    agent_run_id=ctx.agent_run_id, reservation=None
                )
            await self._service(uow).record_agent_run_usage(
                ctx=ctx,
                runtime_profile=runtime_profile,
                usage_data=usage_data,
                status=status,
                reservation=reservation,
            )
            await uow.commit()
