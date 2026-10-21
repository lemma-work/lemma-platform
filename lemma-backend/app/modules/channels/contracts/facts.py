"""What the channels module announces.

Emitted in the transaction that made the change, so whatever follows -- an
agent answering a line, a delivery going out, an asker woken -- happens once
the change is committed, and only then. A submodule rather than
``contracts/__init__``: it pulls in the work runtime.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.work import Fact, pod_namespace
from app.modules.channels.domain.entities import (
    ChannelKind,
    QuestionAsker,
    QuestionState,
    SenderKind,
)


class ChannelMessagePosted(Fact):
    """A line was added to a timeline."""

    pod_id: UUID
    channel_id: UUID
    channel_kind: ChannelKind
    message_id: UUID
    seq: int
    sender_kind: SenderKind
    #: The member who wrote it, for a member's line.
    sender_user_id: UUID | None = None
    #: Where it was said, or ``None`` for a line written in Lemma.
    source_id: UUID | None = None
    members_only: bool = False
    thread_root_id: UUID | None = None
    #: The agents that answer it: those it names, and those in the channel
    #: that answer every line. Empty for an agent's own line.
    answering_agent_ids: tuple[UUID, ...] = ()
    #: A contact's channel: who was assigned when the line was written --
    #: whom a contact's line is pushed to, however soon somebody steps in.
    assignee_user_id: UUID | None = None

    def work_namespace(self) -> str:
        return pod_namespace(self.pod_id)


class ChannelDeliveryDue(Fact):
    """A line has a delivery to send outside Lemma: to a place, or a push to
    a member. Whatever owns the platforms sends it."""

    pod_id: UUID
    delivery_id: UUID
    #: ``Delivery.target_key``: the place or member it goes to, so the sends
    #: to one target run one at a time. Empty from an older announcement.
    target: str = ""

    def work_namespace(self) -> str:
        return pod_namespace(self.pod_id)


class ChannelQuestionClosed(Fact):
    """A question stopped waiting: answered, cancelled or expired."""

    pod_id: UUID
    channel_id: UUID
    message_id: UUID
    ref: str
    batch_id: str | None = None
    state: QuestionState
    awaited_user_id: UUID | None = None
    #: What asked it -- each asker hears only its own -- and the
    #: conversation or run that did, while it exists.
    asker: QuestionAsker
    asker_conversation_id: UUID | None = None
    asker_workflow_run_id: UUID | None = None
    asker_key: str | None = None

    def work_namespace(self) -> str:
        return pod_namespace(self.pod_id)


__all__ = ["ChannelDeliveryDue", "ChannelMessagePosted", "ChannelQuestionClosed"]
