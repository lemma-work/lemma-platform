"""Who answers a message nobody addressed, and what happens when nobody does.

Split from ``conversation_turns`` because that file is at the architecture
ratchet's per-file limit, and because these two are one question: the routing
decision and the path it takes when the answer is "nobody". The rest of the
coordinator is about running a turn, which is a different subject.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import (
    AgentRunStartResult,
    MessageDraft,
    MessageKind,
    MessageRole,
)
from app.modules.agent.services.agent_router import (
    InboundMessage,
    RosterAgent,
    routing_is_needed,
)
from app.modules.agent.services.realtime import (
    input_added_payload,
    publish_conversation_event,
)
from app.modules.agent.services.serialization import message_to_payload

#: How much of the conversation the router is shown. Enough to resolve a reply
#: or a follow-up, short enough that routing stays cheap on every message.
_ROUTER_CONTEXT_MESSAGES = 6

#: "The question does not arise" -- distinct from None, which is a router that
#: was asked and chose nobody. Without a third value the two collapse, and a
#: one-agent conversation would fall into the silence path on every message.
UNROUTED = object()


class TurnRoutingMixin:
    async def _route_unaddressed(
        self,
        conversation: Conversation,
        *,
        content: str,
        user_id: UUID,
        pod_id: UUID,
    ) -> Agent | None | object:
        """Who answers a message nobody addressed.

        Three answers, and they are all different: ``UNROUTED`` means the
        question does not arise and the conversation's own agent answers as it
        always has; an agent means route to that one; None means nobody
        answers and the message is simply stored.

        Only rooms with more than one agent in them ever reach a model. That is
        what `routing_is_needed` decides, and it is why the default
        conversation -- one person, one agent -- costs nothing.
        """
        roster = [
            RosterAgent(id=participant.agent_id, name=participant.display_name or "")
            for participant in conversation.participants
            if participant.agent_id is not None
        ]
        message = InboundMessage(text=content)
        if not routing_is_needed(message, roster):
            return UNROUTED
        # Imported here, not at module load: it pulls in pydantic-ai, and this
        # module is on the API's import path. The structural check above is what
        # makes that affordable -- most conversations never reach this line.
        from app.modules.agent.services.agent_router_model import resolve_responder

        # The last few lines, oldest first. Without them every message is
        # judged cold, and "yes please" or "and the other one?" is unroutable
        # on its own while being obvious in context.
        recent, _ = await self.conversation_repository.list_messages(
            conversation_id=conversation.id,
            limit=_ROUTER_CONTEXT_MESSAGES,
        )
        chosen_ids = await resolve_responder(
            message,
            roster,
            user_id=user_id,
            organization_id=conversation.organization_id,
            pod_id=pod_id,
            recent=[
                f"{item.role.value}: {item.text}"
                for item in recent
                if item.text and item.kind is MessageKind.TEXT
            ],
        )
        if not chosen_ids:
            return None
        # One run per conversation is a database invariant
        # (`uq_agent_active_run_per_conversation`), so however many the router
        # names, one of them answers now. They come back most-relevant first.
        chosen = await self.agent_repository.get(chosen_ids[0])
        # The roster came from the conversation's own rows, so a missing agent
        # here means it was deleted between the two reads. Falling back to the
        # conversation's agent is better than dropping the message.
        return chosen if chosen is not None else UNROUTED

    async def _store_without_answering(
        self,
        conversation: Conversation,
        *,
        content: str,
        user_id: UUID,
        message_metadata: dict[str, object] | None,
    ) -> AgentRunStartResult:
        """Keep the message; start no run.

        The ordinary outcome of talking to a person in a room an agent happens
        to be in. The message is stored, published to everyone watching, and
        nothing answers -- so there is no run, and the result says so.
        """
        saved = await self.conversation_repository.append_message(
            conversation_id=conversation.id,
            agent_run_id=None,
            draft=MessageDraft.of_text(
                content,
                role=MessageRole.USER,
                sender_user_id=user_id,
                metadata={**(message_metadata or {}), "answered_by_agent": False},
            ),
        )
        await self.uow.commit()

        async def _publish() -> None:
            await publish_conversation_event(
                conversation.id,
                input_added_payload(None, message_to_payload(saved)),
            )

        self.uow.after_commit(_publish)
        return AgentRunStartResult(
            conversation_id=conversation.id,
            agent_run_id=None,
            started_new_run=False,
        )
