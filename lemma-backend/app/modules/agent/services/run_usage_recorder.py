"""Usage reservation, accumulation, recording and release for agent runs.

Extracted so the background runner and the inline sub-agent/function paths share
one implementation instead of duplicating the plumbing.

Accumulation is the part that is easy to miss. A run's spend used to exist only
in the worker's memory until the run finished, so a worker that was killed took
the tokens it had already bought with it. `accumulate` writes them to the run's
own row as each model request lands, which is what lets both the finalizer and
the orphan reconciler bill a run neither of them watched.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.value_objects import JsonObject
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

    async def accumulate(
        self,
        *,
        agent_run_id: UUID,
        attempt_id: str,
        usage_data,
    ) -> None:
        """Write what this attempt has spent so far, replacing its own last word.

        Absolute rather than incremental, so a repeated write says the same
        thing. Its own transaction, and deliberately not joined to whatever the
        run is doing: this is bookkeeping about the run, and a rollback of the
        run's work must not roll back the record of what that work cost.
        """
        async with self.uow_factory() as uow:
            await ConversationRepository(uow).store_attempt_usage(
                agent_run_id=agent_run_id,
                attempt_id=attempt_id,
                usage=_attempt_row(usage_data),
            )
            await uow.commit()

    async def claim_accumulated(self, *, agent_run_id: UUID) -> JsonObject | None:
        """Take the run's spend, leaving nothing for a second biller."""
        async with self.uow_factory() as uow:
            claimed = await ConversationRepository(uow).claim_accumulated_usage(
                agent_run_id=agent_run_id
            )
            await uow.commit()
            return claimed

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
                repository = ConversationRepository(uow)
                await repository.store_usage_reservation(
                    agent_run_id=ctx.agent_run_id, reservation=None
                )
                # And the accumulation, for the same reason: this row is the
                # settlement of everything the run spent, so anything left
                # behind would be billed a second time by the reconciler.
                await repository.claim_accumulated_usage(agent_run_id=ctx.agent_run_id)
            await self._service(uow).record_agent_run_usage(
                ctx=ctx,
                runtime_profile=runtime_profile,
                usage_data=usage_data,
                status=status,
                reservation=reservation,
            )
            await uow.commit()


def _attempt_row(usage_data) -> JsonObject:
    """One attempt's spend, as the few numbers a usage row is rebuilt from.

    Deliberately not the whole `AgentRunUsage`: what has to survive a dead
    worker is the counts and the model they were bought on. Everything else on
    the record -- who, which pod, which conversation -- is on the run's own row
    already and is read from there when the spend is finally billed.
    """
    metadata = usage_data.metadata or {}
    return {
        "model_name": usage_data.model_name,
        "input_tokens": int(usage_data.input_tokens or 0),
        "output_tokens": int(usage_data.output_tokens or 0),
        "request_count": int(usage_data.request_count or 0),
        "tool_call_count": int(usage_data.tool_call_count or 0),
        "cache_read_tokens": int(metadata.get("cache_read_tokens") or 0),
        "cache_write_tokens": int(metadata.get("cache_write_tokens") or 0),
    }
