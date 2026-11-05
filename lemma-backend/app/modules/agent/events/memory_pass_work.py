"""``agent.memory``: the memory pass a completed run a person waited on is owed.

Written by ``services/run_followups.stage_run_followups`` in the transaction
that completed the run, so a run told "your memory is kept after this turn"
can never end without the item that keeps it.

AUTOMATION, as a conversation title is: nobody waits on it, so it never queues
ahead of a person's turn. One at a time per pod (``memory_pod_key``): every
agent in a pod shares ``/memory/AGENTS.md``, and two passes rewriting it at
once would each drop what the other added.

Retried once. A model or network blip is worth another try, and so is a
keeper that ran out of steps before writing anything (``MemoryPassUnfinished``;
one that wrote something has kept it). A refusal that would only repeat -- the
person over their usage limit, the model no longer configured, the turn too
long to read within the pass's own limits -- is not.
"""

from __future__ import annotations

from datetime import timedelta

from pydantic_ai.exceptions import UsageLimitExceeded

from app.core.domain.errors import DomainError
from app.core.infrastructure.work import (
    Exponential,
    JobContext,
    PermanentFailure,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
)
from app.modules.agent.events.turn_payloads import MemoryPassWork
from app.modules.agent.events.work_kinds import AGENT_MEMORY

MEMORY_PASS_RETRY = RetryPolicy(
    max_attempts=2,
    max_lease_losses=2,
    unavailable_budget=timedelta(hours=1),
    backoff=Exponential(
        base=timedelta(seconds=10), cap=timedelta(minutes=2), jitter=0.2
    ),
)


async def keep_memory(ctx: JobContext, payload: MemoryPassWork) -> None:
    from app.modules.agent.services.harness_wiring import worker_uow_factory
    from app.modules.agent.services.memory_pass import MemoryPass

    ctx.raise_if_stopping()
    try:
        await MemoryPass(worker_uow_factory()).keep(
            conversation_id=payload.conversation_id,
            run_id=payload.run_id,
            user_id=payload.user_id,
        )
    except DomainError as exc:
        raise PermanentFailure(exc.message) from exc
    except UsageLimitExceeded as exc:
        raise PermanentFailure(str(exc)) from exc


def register_memory_pass_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=AGENT_MEMORY,
        cls=WorkClass.AUTOMATION,
        mode=WorkMode.JOB,
        payload=MemoryPassWork,
        retry=MEMORY_PASS_RETRY,
        concurrency=1,
    )(keep_memory)


__all__ = ["MEMORY_PASS_RETRY", "keep_memory", "register_memory_pass_work"]
