"""``surface.reply_for_run``: draining what was queued before one answer path.

A finished run's answer is said by one subscriber now, the agent module's
``agent.channel_reply`` (``agent/services/run_answers``): at the place of the
lines it answered, in Lemma or -- as the agent's line with a delivery there,
posted through ``contracts.egress.post_answer_at_place`` -- on a platform.
Nothing subscribes this kind any more. Its handler stays for one release, so
an item written by a run that finished before the change is still carried;
it hands the run to the same path, which is safe to run twice (the answer is
the run's one line, and its delivery is unique per place). The release after
removes the kind and this module.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.work import (
    FastContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    registry as default_registry,
)
from app.modules.agent.contracts.run_answers import post_run_answer
from app.modules.agent_surfaces.events.work_kinds import SURFACE_REPLY_FOR_RUN


class RunReplyPayload(BaseModel):
    """The fields of ``AgentRunFinished`` this needs; the rest are ignored."""

    run_id: UUID
    conversation_id: UUID
    pod_id: UUID
    status: str


async def drain_run_reply(ctx: FastContext, payload: RunReplyPayload) -> None:
    """A run queued for its answer before ``agent.channel_reply`` said every
    run's: only an answer or a failure was ever queued here."""
    await post_run_answer(
        ctx.uow,
        run_id=payload.run_id,
        conversation_id=payload.conversation_id,
        answered=payload.status == "COMPLETED",
    )


def register_reply_for_run_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=SURFACE_REPLY_FOR_RUN,
        cls=WorkClass.DELIVERY,
        mode=WorkMode.FAST,
        payload=RunReplyPayload,
    )(drain_run_reply)


register_reply_for_run_work(default_registry)
