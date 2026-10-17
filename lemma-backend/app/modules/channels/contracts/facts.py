"""What the channels module announces.

``ChannelMessagePosted`` is emitted in the transaction that adds a line to a
timeline, for whatever must follow it -- a member's post going out to the
place it was written for, a contact's agent hearing a member stepped in.
A submodule rather than ``contracts/__init__``: it pulls in the work runtime.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.work import Fact, pod_namespace
from app.modules.channels.domain.entities import SenderKind


class ChannelMessagePosted(Fact):
    pod_id: UUID
    channel_id: UUID
    message_id: UUID
    sender_kind: SenderKind
    #: Where it was said, or ``None`` for a line written in Lemma.
    source_id: UUID | None = None
    members_only: bool = False

    def work_namespace(self) -> str:
        return pod_namespace(self.pod_id)


__all__ = ["ChannelMessagePosted"]
