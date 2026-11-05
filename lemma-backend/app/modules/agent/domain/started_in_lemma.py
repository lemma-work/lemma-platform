"""Whether a person started a conversation in Lemma itself.

The workspace module asks this before letting a run onto a person's own
machine. Pure: a view over the conversation's row, nothing read.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.value_objects import ConversationType


def started_in_lemma(conversation: Conversation, user_id: UUID) -> bool:
    """Whether ``user_id`` started this conversation in Lemma itself.

    A person's default machine holds their logins and their files, so a run
    reaches it only when that person asked for the run in Lemma -- never one
    started by a message on a channel, a contact, a schedule's task, or
    anybody else.
    """
    return (
        conversation.user_id == user_id
        and conversation.channel_id is None
        and conversation.contact_id is None
        and conversation.type is not ConversationType.TASK
    )
