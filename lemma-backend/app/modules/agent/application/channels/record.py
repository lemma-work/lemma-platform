"""What a chat surface writes into a conversation without starting a run.

A notification sent on a member's behalf, a line about a delivery that may not
have arrived, a member's follow-up to a contact, and what somebody outside the
pod wrote while nobody may answer them yet. Each belongs to no run: it is there
so the person reading the conversation in Lemma -- and the agent, when it next
answers -- sees what was said elsewhere.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.contact_history import FOLLOW_UP_LINE_KEY
from app.modules.agent.domain.held_turns import held_messages
from app.modules.agent.domain.value_objects import MessageDraft
from app.modules.agent.infrastructure.db.messages import Messages


class RecordInConversation:
    """Appends what a surface heard or sent, in no run."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.messages = Messages(uow)

    @classmethod
    def build(cls, uow: SqlAlchemyUnitOfWork) -> RecordInConversation:
        return cls(uow)

    async def notification(
        self, *, conversation_id: UUID, message: str, notification_id: UUID
    ) -> None:
        """An outbound notification in the recipient's thread, once: a retried
        send of the same notification finds it there and adds no second copy.
        It is what lets the recipient's agent answer "yes" hours later against
        a question it can see in its own history."""
        key = str(notification_id)
        if await self.messages.notification_keyed(
            conversation_id, key="notification_id", value=key
        ):
            return
        await self.messages.append_message(
            conversation_id=conversation_id,
            agent_run_id=None,
            draft=MessageDraft.of_notification(
                message, metadata={"notification_id": key}
            ),
        )

    async def delivery_notice(
        self, *, conversation_id: UUID, message: str, metadata: dict[str, str]
    ) -> None:
        """What the person reading in Lemma should know about a message sent
        on their behalf elsewhere -- that it may not have arrived."""
        await self.messages.append_message(
            conversation_id=conversation_id,
            agent_run_id=None,
            draft=MessageDraft.of_notification(message, metadata=dict(metadata)),
        )

    async def follow_up(
        self,
        *,
        conversation_id: UUID,
        line_id: UUID,
        sent_by_user_id: UUID,
        delivered: bool | None = None,
    ) -> None:
        """Where a member's line to a contact falls in their conversation.

        It keeps which line, never the line's words: those are read from the
        channel whenever the conversation is, so an edit reads as it says now
        and a deleted line is gone. ``delivered`` is False for one the platform
        refused. One mark per line: a retried send brings ``delivered`` up to
        date rather than adding a second.
        """
        earlier = await self.messages.notification_keyed(
            conversation_id, key=FOLLOW_UP_LINE_KEY, value=str(line_id)
        )
        if earlier is not None:
            if delivered is not None:
                await self.messages.merge_message_metadata(
                    earlier.id, {"delivered": delivered}
                )
            return
        metadata: dict[str, object] = {
            "follow_up": True,
            "sent_by_user_id": str(sent_by_user_id),
            FOLLOW_UP_LINE_KEY: str(line_id),
        }
        if delivered is not None:
            metadata["delivered"] = delivered
        await self.messages.append_message(
            conversation_id=conversation_id,
            agent_run_id=None,
            draft=MessageDraft.of_notification("", metadata=metadata),
        )

    async def held_turn(
        self,
        *,
        conversation_id: UUID,
        text: str,
        metadata: dict[str, object],
        reply: str | None,
    ) -> None:
        """What somebody outside the pod wrote, kept with no run started.

        ``reply``, when given, is what they were told instead of an answer,
        written as the bot's so the thread shows it. Both are marked held
        (``held_turns``): the mark carries them to the agent once it answers
        them again.
        """
        for draft in held_messages(text, metadata, reply=reply):
            await self.messages.append_message(
                conversation_id=conversation_id, agent_run_id=None, draft=draft
            )
