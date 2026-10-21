"""A finished run's answer, as the agent's line at the place it was asked.

The answer is posted first -- the agent's line in the channel the place
belongs to, said for the person the conversation answers -- with a delivery to
that place, which ``channel_deliveries`` sends. One outbound path: the line is
the record, the delivery row is the only idempotency record of the send, and
the platform's id for the message lands on it.

A run is one line however often this runs: the line's client id is the run,
and its delivery is unique per (line, place). A failed run's line is its
failure notice, which the send offers a retry for. A web chat shows its
answers itself, so its delivery is ``SENT`` here and nothing is sent.

No network: written in a FAST item (``events/reply_for_run_work``).
"""

from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError

from app.core.authorization.delegation import (
    DEFAULT_RESPONDER_NAME,
    effective_agent_id,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent.contracts.run_failures import user_safe_stored_reason
from app.modules.agent.contracts.run_replies import (
    RunReplyMaterial,
    answer_line_client_id,
)
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.platforms.rendering import sanitize_user_visible_text
from app.modules.agent_surfaces.services.channel_lines import (
    WEB,
    answered_for,
    shown_on_web,
)
from app.modules.agent_surfaces.services.place_threads import THREADED
from app.modules.agent_surfaces.services.progress_events import failure_notice
from app.modules.channels.contracts import (
    ChannelMessage,
    NewLine,
    Sender,
    Source,
    SourceKind,
    post,
)
from app.modules.channels.contracts.lines import thread_root_at
from app.modules.channels.contracts.places import ReplyAddress

logger = get_logger(__name__)

#: What a person reads when the run wrote only reasoning and no answer.
ALL_REASONING_REPLY = (
    "I thought that through but never wrote the answer. "
    "Ask me again and I'll give it another go."
)


def answer_text(material: RunReplyMaterial, *, platform: str) -> str | None:
    """What the run's line says: its answer, or -- failed -- why and what next."""
    if material.status == "FAILED":
        return failure_notice(user_safe_stored_reason(material.error), platform)
    if not material.answer:
        return None
    # Never reasoning tokens as a message. An answer that was nothing else is
    # said to have been, rather than met with silence.
    return sanitize_user_visible_text(material.answer) or ALL_REASONING_REPLY


async def post_run_reply(
    uow: SqlAlchemyUnitOfWork,
    *,
    run_id: UUID,
    conversation_id: UUID,
    address: ReplyAddress,
    material: RunReplyMaterial,
) -> ChannelMessage | None:
    """Post the run's answer at ``address`` and plan its delivery there."""
    source = address.source
    # The place's platform, not a missing surface: a place whose bot was
    # removed has no surface either, and its answer is not shown anywhere --
    # its delivery says so (``channel_deliveries``).
    web = source.platform == WEB
    if web and material.status == "FAILED":
        # The web chat says a run failed on its own page.
        return None
    text = answer_text(material, platform=source.platform)
    conversation = await agent_conversations.surface_conversation(uow, conversation_id)
    if text is None or conversation is None:
        return None
    agent_id = effective_agent_id(conversation.agent_id, pod_id=conversation.pod_id)
    event = _answered_event(address)
    line = await post(
        uow,
        channel_id=source.channel_id,
        line=NewLine(
            sender=Sender.agent(
                agent_id,
                on_behalf_of=answered_for(conversation.audience, conversation.user_id),
                display_name=await _agent_name(uow, agent_id),
            ),
            text=text,
            target_source_id=source.id,
            client_message_id=answer_line_client_id(run_id),
            conversation_id=conversation_id,
            agent_run_id=run_id,
            # The thread it goes to, so the place knows the agent is in it --
            # and a Slack or Teams thread is a thread here too.
            external_thread_id=event.external_thread_id if event else None,
            thread_root_id=await _thread_root(uow, source, event),
        ),
        deliver_to=[source.id],
    )
    if line is not None and web:
        await shown_on_web(uow, line, source)
    return line


def _answered_event(address: ReplyAddress) -> ParsedInboundSurfaceEvent | None:
    if not address.ref:
        return None
    try:
        return ParsedInboundSurfaceEvent.model_validate(address.ref)
    except ValidationError:
        return None


async def _thread_root(
    uow: SqlAlchemyUnitOfWork,
    source: Source,
    event: ParsedInboundSurfaceEvent | None,
) -> UUID | None:
    if event is None or source.kind is not SourceKind.CHAT:
        return None
    if source.platform not in THREADED:
        return None
    return await thread_root_at(
        uow, source_id=source.id, external_thread_id=event.external_thread_id
    )


async def _agent_name(uow: SqlAlchemyUnitOfWork, agent_id: UUID) -> str:
    """The name the answer goes out under: the pod's own agent has the
    product's responder name, not its internal one."""
    agent = await agent_conversations.surface_agent_identity(uow, agent_id)
    if agent is None or agent.is_pod_default:
        return DEFAULT_RESPONDER_NAME
    return agent.name


__all__ = ["ALL_REASONING_REPLY", "answer_text", "post_run_reply"]
