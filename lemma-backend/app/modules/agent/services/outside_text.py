"""What a run reads from outside the pod, told to the authorizer.

A member's run in a group is shown everything said there, what people outside
the pod wrote marked as theirs (``channels.contracts.backgrounds``). The prompt
tells the model to read those lines as information; the authorizer asks the
person before the run acts beyond reading and writing the pod's own records
and files (``core.authorization.outside_text``). This is what keeps the
authorizer's answer true to what the run actually has in front of it:

* as a turn starts, from its whole prompt -- the backgrounds its messages
  carry, and what the channel reads still in its history returned;
* when a message steered into the run brings a background of its own;
* when the run reads a channel itself and a line in it is from outside.

Kept per conversation, which is one person's in a group: another member's run
there is decided on its own prompt.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from uuid import UUID

from app.core.authorization.outside_text import (
    forget_outside_text,
    note_outside_text,
)
from app.modules.agent.domain.entities import Message

#: Tools whose results are lines of the pod's channels, each marked by whether
#: somebody outside the pod wrote it.
_CHANNEL_READS = frozenset({"channel_read", "channel_search"})


def carries_outside_text(messages: Iterable[Message]) -> bool:
    """Whether any of these messages puts a line from outside the pod in front
    of the model."""
    return any(_reads_outside(message) for message in messages)


def _any_outside(lines: object) -> bool:
    """Whether a list of lines, as backgrounds and channel reads give them,
    holds one written outside the pod."""
    return isinstance(lines, list) and any(
        isinstance(line, Mapping) and line.get("outside_pod") for line in lines
    )


def _background_reads_outside(message: Message) -> bool:
    """Whether the background read in with this message holds a stranger's line."""
    return _any_outside((message.metadata or {}).get("channel_context"))


def _reads_outside(message: Message) -> bool:
    if _background_reads_outside(message):
        return True
    result = message.tool_result
    return (
        message.tool_name in _CHANNEL_READS
        and isinstance(result, Mapping)
        and _any_outside(result.get("lines"))
    )


async def note_what_the_turn_reads(
    conversation_id: UUID, messages: Iterable[Message]
) -> None:
    """Set or clear the conversation's mark from the prompt a turn starts with.

    Cleared as well as set: a member who moved on to a chat with nobody from
    outside in it is not asked to approve their own work because of last
    week's.
    """
    if carries_outside_text(messages):
        await note_outside_text(str(conversation_id))
    else:
        await forget_outside_text(str(conversation_id))


async def note_steered_messages(messages: Sequence[Message]) -> None:
    """Mark the conversation when messages steered into a run carry a line
    from outside. Only ever set mid-run: the turn already reads what it read.
    A steered message is a person's, so its background is all it brings."""
    for message in messages:
        if _background_reads_outside(message):
            await note_outside_text(str(message.conversation_id))
            return


async def note_outside_text_read(conversation_id: UUID | None) -> None:
    """Mark the conversation: its run has just read a line from outside."""
    if conversation_id is not None:
        await note_outside_text(str(conversation_id))
