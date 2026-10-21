"""``surface.reply_for_run``: a finished run's answer, as a line owed its place.

Subscribed to the agent module's ``AgentRunFinished``, so the item is written
in the transaction that finished the run: once a run has an outcome, the reply
it owes is on record. It is the only way a run's answer reaches a surface: the
run's observer only shows progress.

The answer is posted as the agent's line at the place the run was asked, with
a delivery to that place (``services/run_reply_lines``), which the channel
delivery executor sends -- finishing a live stream in place, carrying the
run's held files, offering a retry on a failure notice -- and records with
the platform's id (``services/channel_deliveries``). A redelivered fact is
the same line and the same delivery: one line, one send.

An answer to something asked in the web chat is shown by the widget: its line
is written and its delivery ``SENT`` at once, and no platform thread the same
conversation also has is sent anything. Nor is one to something asked in
Lemma itself: the agent module answers it there, in the same thread.

FAST: a few rows read, one line written. No network in this module.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.work import (
    FastContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.agent.contracts.run_replies import run_reply_material
from app.modules.agent_surfaces.events.work_kinds import (
    SURFACE_REPLY_FOR_RUN,
    run_reply_key,
)
from app.modules.agent_surfaces.services.run_reply_lines import post_run_reply
from app.modules.channels.contracts.lines import last_heard_line
from app.modules.channels.contracts.places import reply_address

_REPLYING_STATUSES = frozenset({"COMPLETED", "FAILED"})


class RunReplyPayload(BaseModel):
    """The fields of ``AgentRunFinished`` this needs; the rest are ignored."""

    run_id: UUID
    conversation_id: UUID
    pod_id: UUID
    status: str


def owes_a_reply(fact: AgentRunFinished) -> bool:
    """A run that ended with an answer or a failure; not one that paused.

    Whether the conversation is on a surface at all is read in the handler,
    from the link -- one cheap item per finished run rather than a predicate
    trusting a flag the finisher had to remember to set.
    """
    return fact.status in _REPLYING_STATUSES and fact.conversation_status != "WAITING"


async def post_run_answer(ctx: FastContext, payload: RunReplyPayload) -> None:
    material = await run_reply_material(ctx.uow, payload.run_id)
    if material is None or material.private:
        return
    if await _answered_in_lemma(ctx, payload):
        return
    address = await reply_address(ctx.uow, payload.conversation_id)
    if address is None:
        return
    await post_run_reply(
        ctx.uow,
        run_id=payload.run_id,
        conversation_id=payload.conversation_id,
        address=address,
        material=material,
    )


async def _answered_in_lemma(ctx: FastContext, payload: RunReplyPayload) -> bool:
    """Whether the run answers something asked in Lemma -- which the agent
    module answers there, in the same thread (``agent.channel_reply``), and
    which no platform is sent."""
    heard = await last_heard_line(ctx.uow, payload.conversation_id)
    return heard is not None and heard.source_id is None


def register_reply_for_run_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=SURFACE_REPLY_FOR_RUN,
        cls=WorkClass.DELIVERY,
        mode=WorkMode.FAST,
        payload=RunReplyPayload,
    )(post_run_answer)


register_reply_for_run_work(default_registry)
facts.subscribe(
    AgentRunFinished,
    kind=SURFACE_REPLY_FOR_RUN,
    cls=WorkClass.DELIVERY,
    when=owes_a_reply,
    dedup=lambda fact: run_reply_key(fact.run_id),
)
