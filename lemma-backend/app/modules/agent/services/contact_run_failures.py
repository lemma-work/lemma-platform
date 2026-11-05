"""A contact's run that failed: a person is called for, and nobody was answered.

A contact who wrote and whose run failed was told nothing on a web page, and
nobody in the pod heard of it: the channel did not ask for a person, and the
contact was still counted as answered against the organization's cap -- they
were marked as the run was let in (``agent_surfaces.services.outside_cap``).

So when a contact's run fails, their channel asks for a person, as it does
when the agent is off or the cap is reached, and the mark is taken back while
no run of theirs has ever got further than failing. Their page hears it from
the run's own ``error`` frame (``contracts.visitor_stream``); a platform's
failure notice is ``agent_surfaces``'.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import exists, select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.contracts import channel_conversations
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.infrastructure.models.conversation import (
    AgentRunModel,
    ConversationModel,
)
from app.modules.channels.contracts import call_for_person
from app.modules.contacts.contracts import note_never_answered

logger = get_logger(__name__)

#: Why a contact's channel waits on a person after their run failed.
RUN_FAILED_REASON = "The agent couldn't answer them"


async def after_contact_run(
    uow: SqlAlchemyUnitOfWork, *, run_id: UUID, conversation_id: UUID, status: str
) -> None:
    """After a contact's run ended: one that failed calls for a person, and
    stops counting the contact as answered if no run ever answered them."""
    if status != AgentRunStatus.FAILED.value:
        return
    conversation = await uow.session.get(ConversationModel, conversation_id)
    if conversation is None or conversation.contact_id is None:
        return
    channel_id = await channel_conversations.channel_of_conversation(
        uow, conversation_id
    )
    called = (
        await call_for_person(uow, channel_id=channel_id, reason=RUN_FAILED_REASON)
        if channel_id is not None
        else False
    )
    uncounted = False
    if not await _ever_got_further(uow, conversation.pod_id, conversation.contact_id):
        uncounted = await note_never_answered(
            uow, pod_id=conversation.pod_id, contact_id=conversation.contact_id
        )
    logger.info(
        "agent.contact_run.failed.observed",
        pod_id=str(conversation.pod_id),
        conversation_id=str(conversation_id),
        run_id=str(run_id),
        called_for_person=called,
        uncounted=uncounted,
    )


async def _ever_got_further(
    uow: SqlAlchemyUnitOfWork, pod_id: UUID, contact_id: UUID
) -> bool:
    """Whether any run for this contact did anything but fail -- answered,
    was stopped by a member, or is going now. Each holds their place."""
    run, conversation = AgentRunModel, ConversationModel
    return bool(
        await uow.session.scalar(
            select(
                exists().where(
                    run.conversation_id == conversation.id,
                    conversation.pod_id == pod_id,
                    conversation.contact_id == contact_id,
                    run.status != AgentRunStatus.FAILED.value,
                )
            )
        )
    )


__all__ = ["RUN_FAILED_REASON", "after_contact_run"]
