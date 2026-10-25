"""What a contact merge copied from a conversation that lost its seat, as a run reads it.

Two contacts that turn out to be one person each had a conversation with the
same agent in their channel. One keeps the seat, and what the person saw of
the other is copied into it, in no run, each copy marked with the conversation
it came from (``contracts/folded_history``). A run reads its history by run,
so the copies reach the agent answering the kept conversation only because
they are carried on purpose (``services/runtime_history``), as notifications
and held messages are. Three things set them apart from those.

They are where the merge put them: after what the kept conversation held when
it ran, which is where the person's chat shows them too, and not where they
fall in time, which nothing kept. Read as more of this chat, the other chat's
last line would pass for the newest thing said, so each stretch of copies is
opened and closed by a notice saying it is another chat's.

A copy was written when the merge ran, so its own time is the merge's. A
person's message is read with when they sent it; a copy is read with no time
rather than a wrong one (``harnesses/user_turn_text``).

And a chat folded in can be long. Carried under the backstop notifications
share, it would take every place in it and push them out, so copies have a
bound of their own, and the notice says when it left some out.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.modules.agent.domain.entities import Message, MessageKind, MessageRole
from app.modules.agent.domain.history_notices import SYNTHETIC_NOTICE_PREFIX

#: Opens each stretch of copies in the history a run reads.
FOLDED_CHAT_OPENS = (
    f"{SYNTHETIC_NOTICE_PREFIX} What follows was said in another chat, joined to "
    "this one when two contacts turned out to be one person. It sits after what "
    "this chat held when they were joined, which says nothing about when it was "
    "said."
)

#: Ends the first stretch's opening when the bound left copies out.
FOLDED_CHAT_CUT = " Earlier messages joined to this chat are not shown."

#: Closes each stretch, so what this chat says next is not read as the other's.
FOLDED_CHAT_ENDS = (
    f"{SYNTHETIC_NOTICE_PREFIX} The other chat ends here, and this one carries on."
)


def folded_from(message: Message) -> UUID | None:
    """The conversation a copy came from; None for a message nobody copied."""
    return message.state.folded_from


def newest_folds(
    messages: Sequence[Message], *, keep: int
) -> tuple[list[Message], bool]:
    """``messages`` with only the newest ``keep`` copies, and whether any went.

    ``messages`` are oldest first, as the run-less read returns them; whatever
    is not a copy is kept.
    """
    copies = [
        index
        for index, message in enumerate(messages)
        if folded_from(message) is not None
    ]
    left_out = set(copies[: max(0, len(copies) - keep)])
    kept = [message for index, message in enumerate(messages) if index not in left_out]
    return kept, bool(left_out)


def framed_folds(messages: Sequence[Message], *, cut: bool = False) -> list[Message]:
    """``messages`` with every stretch of copies between the two notices.

    A stretch is copies of one conversation side by side, as a merge writes
    them. ``cut`` says the bound left some out; the first stretch says so.
    """
    newest = max((message.sequence for message in messages), default=0)
    framed: list[Message] = []
    stretch: str | None = None
    for message in messages:
        fold = folded_from(message)
        if fold != stretch:
            if stretch is not None:
                framed.extend(_closing(framed[-1], newest=newest))
            if fold is not None:
                opening = FOLDED_CHAT_OPENS + (FOLDED_CHAT_CUT if cut else "")
                framed.append(_notice(opening, beside=message, kind="opened"))
                cut = False
            stretch = fold
        framed.append(message)
    if stretch is not None:
        framed.extend(_closing(framed[-1], newest=newest))
    return framed


def _closing(last: Message, *, newest: int) -> list[Message]:
    """The notice after a stretch, unless nothing follows it: a notice after
    everything else would be read as the turn to answer."""
    if last.sequence >= newest:
        return []
    return [_notice(FOLDED_CHAT_ENDS, beside=last, kind="ended")]


def _notice(text: str, *, beside: Message, kind: str) -> Message:
    return Message(
        conversation_id=beside.conversation_id,
        # Ties the copy it stands beside. The history is sorted stably, so
        # it keeps the side of it it was put on.
        sequence=beside.sequence,
        agent_run_id=None,
        # User role, as every notice the history carries (``runtime_history``).
        role=MessageRole.USER,
        kind=MessageKind.NOTIFICATION,
        text=text,
        metadata={"synthetic": True, "summary_kind": f"folded_chat_{kind}"},
    )
