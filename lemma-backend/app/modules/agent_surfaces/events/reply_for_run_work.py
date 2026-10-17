"""``surface.reply_for_run``: a finished run's answer, owed to its surface thread.

Subscribed to the agent module's ``AgentRunFinished``, so the item is written
in the transaction that finished the run: once a run has an outcome, the reply
it owes its thread is on record, and the window in which a worker could die
between the run ending and the send -- with nothing left to resend from -- is
gone. It is the only way a run's answer reaches a surface: the run's observer
only shows progress.

The text is not copied here. The row names the run, and the sender renders it
from the run's stored output (or its failure) when it sends, so a run's reply
is one row however late it goes.

An answer to something asked in the web chat is not a platform's to send: the
widget shows it, and it goes on the channel's timeline here instead -- never
to a platform thread the same conversation also has.

FAST: one run row, one link row, one insert. No network in this module.
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
from app.modules.channels.contracts.places import reply_address
from app.modules.agent_surfaces.domain.deliveries import (
    ConversationTarget,
    DeliveryContent,
)
from app.modules.agent_surfaces.events.work_kinds import (
    SURFACE_REPLY_FOR_RUN,
    run_reply_key,
)
from app.modules.agent_surfaces.services.channel_lines import note_answered_on
from app.modules.agent_surfaces.services.surface_deliveries import (
    conversation_destination,
    run_reply_idempotency_key,
    stage_delivery,
)

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


async def stage_run_reply(ctx: FastContext, payload: RunReplyPayload) -> None:
    material = await run_reply_material(ctx.uow, payload.run_id)
    if material is None or material.private:
        return
    failed = payload.status == "FAILED"
    if not failed and not material.answer:
        return
    address = await reply_address(ctx.uow, payload.conversation_id)
    answering = address.source if address is not None else None
    if answering is not None and answering.surface_id is None:
        # Asked in the web chat, which shows the answer itself: it goes on the
        # channel's timeline there, and to no platform thread the
        # conversation also has.
        if material.answer:
            await note_answered_on(
                ctx.uow,
                source=answering,
                conversation_id=payload.conversation_id,
                text=material.answer,
            )
        return
    destination, surface_id = await conversation_destination(
        ctx.uow, payload.conversation_id
    )
    if surface_id is None:
        return
    await stage_delivery(
        ctx.uow,
        idempotency_key=run_reply_idempotency_key(payload.run_id),
        target=ConversationTarget(conversation_id=payload.conversation_id),
        content=DeliveryContent(
            text="",
            purpose="failure" if failed else "answer",
            metadata={"retry_action": True} if failed else {},
            attach_files_of_run=None if failed else payload.run_id,
        ),
        destination_key=destination,
        pod_id=payload.pod_id,
        surface_id=surface_id,
        conversation_id=payload.conversation_id,
        agent_run_id=payload.run_id,
        work=ctx.work,
    )


def register_reply_for_run_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=SURFACE_REPLY_FOR_RUN,
        cls=WorkClass.DELIVERY,
        mode=WorkMode.FAST,
        payload=RunReplyPayload,
    )(stage_run_reply)


register_reply_for_run_work(default_registry)
facts.subscribe(
    AgentRunFinished,
    kind=SURFACE_REPLY_FOR_RUN,
    cls=WorkClass.DELIVERY,
    when=owes_a_reply,
    dedup=lambda fact: run_reply_key(fact.run_id),
)
