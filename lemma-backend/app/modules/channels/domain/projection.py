"""Who may read which line of a channel. One rule, in one place.

A channel merges several sources into one timeline, and the people reading it
are not all entitled to all of it. Every reader -- the timeline a member opens,
the background a run is given, a search -- asks this module, so the answer
cannot drift between them:

* **A member** reads everything: every source, every private note, every
  answer whoever it was made for. What a contact wrote reaches a member's run
  marked as somebody outside the pod, never as an instruction. Where a member
  reads the timeline themselves, though, an answer an agent made with another
  member's own access is shown as given but not what it said
  (``withheld_from``): they may not be able to see what it was made from.
* **A contact** -- a run answering them -- reads only what was said where they
  are: the lines from their own source, and none of those an agent wrote with
  a member's own access. Never a private note. A line is where they are when
  it was heard there or carried there from Lemma (``place_id``).

Whether a member's answer is kept to them is the group's choice
(``channels.answers_shared``): asked in front of the group, it is the
group's to read -- posted shared (``shared_when_posted``) -- unless the group
keeps answers to whoever asked until they share them.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.channels.domain.entities import (
    ChannelKind,
    ChannelMessage,
    Principal,
    Sender,
    SenderKind,
)


class Reader(BaseModel):
    """Who is reading: a member, or a contact where they are speaking from."""

    model_config = ConfigDict(frozen=True)

    #: The contact reading, or ``None`` for a member.
    contact_id: UUID | None = None
    #: The source the contact is reading from. A contact reads nothing else.
    source_id: UUID | None = None

    @classmethod
    def member(cls) -> Reader:
        return cls()

    @classmethod
    def contact(cls, contact_id: UUID, *, source_id: UUID | None) -> Reader:
        return cls(contact_id=contact_id, source_id=source_id)

    @property
    def is_member(self) -> bool:
        return self.contact_id is None


def may_read(reader: Reader, message: ChannelMessage) -> bool:
    """Whether ``reader`` may see ``message``."""
    if reader.is_member:
        return True
    if message.members_only:
        return False
    if reader.source_id is None or message.place_id != reader.source_id:
        return False
    return not message.sender.used_a_members_access


def readable(
    reader: Reader, messages: Iterable[ChannelMessage]
) -> list[ChannelMessage]:
    """The messages ``reader`` may see, in the order given."""
    return [message for message in messages if may_read(reader, message)]


def withheld_from(
    message: ChannelMessage, *, user_id: UUID, in_their_direct: bool = False
) -> bool:
    """Whether the member ``user_id``, reading the timeline, is shown only that
    this answer was given -- not what it said.

    An agent's answer made with one member's own access is that member's to
    read. It was posted where the people in the chat read it, but a member of
    the pod reading the timeline need not be one of them, and may not be able
    to see what it was made from. One whose principal is no longer known is
    withheld from everybody. Never withheld: a line in the reader's own direct
    channel, which the agent posted there to tell them, a question put to the
    reader, which they have to read to answer, and an answer its member shared
    with the channel (``shareable_by``).
    """
    sender = message.sender
    if sender.kind is not SenderKind.AGENT or not sender.used_a_members_access:
        return False
    if in_their_direct or message.shared_at is not None:
        return False
    question = message.question
    if question is not None and question.awaits == Principal.member(user_id):
        return False
    behalf = sender.on_behalf_of
    return behalf is None or behalf.user_id != user_id


def shared_when_posted(
    sender: Sender, *, kind: ChannelKind, answers_shared: bool
) -> bool:
    """Whether a line is shared with the channel as it is written: an
    agent's answer made with the asking member's own access, in a group that
    shows its agents' answers to everybody (``answers_shared``). Who asked in
    front of the group asked the group; who wanted it private would have
    asked in their own channel. A DIRECT channel has one reader anyway, and a
    CONTACT one keeps today's rule; an answer nobody can say was for whom is
    never shared (``withheld_from`` fails closed)."""
    behalf = sender.on_behalf_of
    return (
        kind is ChannelKind.GROUP
        and answers_shared
        and sender.kind is SenderKind.AGENT
        and behalf is not None
        and behalf.user_id is not None
    )


def asker_only(message: ChannelMessage, *, in_a_direct: bool = False) -> bool:
    """Whether this answer is read in full only by the member it was made for:
    an agent's line made with their own access, not shared, outside a direct
    channel (where its one member is the asker anyway). Others in the channel
    are shown that it was given (``withheld_from``)."""
    sender = message.sender
    return (
        sender.kind is SenderKind.AGENT
        and sender.used_a_members_access
        and sender.on_behalf_of is not None
        and sender.on_behalf_of.user_id is not None
        and message.shared_at is None
        and not message.is_deleted
        and not in_a_direct
    )


def shareable_by(
    message: ChannelMessage, *, user_id: UUID, in_a_direct: bool = False
) -> bool:
    """Whether ``user_id`` may share this answer with the channel: it is read
    only by them, and was made for them -- with their access, so whether the
    others may read it is theirs to decide. Never with a contact: sharing
    opens it to the pod's members, not to anybody outside (``may_read``)."""
    behalf = message.sender.on_behalf_of
    return (
        asker_only(message, in_a_direct=in_a_direct)
        and behalf is not None
        and behalf.user_id == user_id
    )
