"""What somebody outside the pod said while a person, not the agent, had them.

A contact's turn starts no run while a member answers them by hand -- one
stepped in, or switched their channel's agents off -- or while the
organization's contacts cap is reached (``agent_surfaces``' ``outside_cap``).
What they wrote is still kept in their conversation, in no run, and so is what
they were told instead of an answer (``contact_conversations.hold_outside_turn``).

A run reads its history by run, so a message in none never reaches the model
unless it is carried on purpose. Uncarried, the agent answering them once the
hold ends reads their newest message with nothing they said while they waited,
and answers as if there were no earlier thread. ``held_for_a_person`` is what
carries it (``runtime_history``). The server sets it where a held message is
written; it is never taken from what a caller passes.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.modules.agent.domain.message_state import MessageState
from app.modules.agent.domain.queued_messages import SERVER_OWNED_KEYS
from app.modules.agent.domain.value_objects import MessageDraft, MessageRole

#: On both messages of a held turn: the outsider's own, and the line they were
#: told in place of an answer. Nothing that would label a message as a
#: member's in the history (``lemma_label``), or drop it as a member's note
#: (``without_private_runs``): a held message's state is this and only this.
_HELD = MessageState(held_for_a_person=True)


def held_messages(
    text: str, metadata: Mapping[str, object] | None, *, reply: str | None
) -> list[MessageDraft]:
    """What a held turn keeps: the outsider's message and, when they were told
    something in place of an answer, that line, written as the bot's.

    Their message carries nothing that says a member wrote it. It is their own
    words, never a member's note, and carried into the history under a note's
    label it would be read with the member's authority.
    """
    theirs = {
        key: value
        for key, value in (metadata or {}).items()
        if key not in SERVER_OWNED_KEYS
    }
    drafts = [
        MessageDraft.of_text(text, role=MessageRole.USER, metadata=theirs, state=_HELD)
    ]
    if reply:
        drafts.append(
            MessageDraft.of_text(reply, metadata={"handed_off": True}, state=_HELD)
        )
    return drafts
