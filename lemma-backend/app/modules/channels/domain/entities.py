"""A channel: one shared place where a pod's members, contacts and agents talk.

A channel holds a timeline -- every message people saw, who said it, when and
where -- and is fed by any number of **sources**: a WhatsApp chat, a Telegram
group, a Slack channel, an email thread, a web chat. A source joins a channel
only because the channel was made for it or a member linked it; nothing infers
that two places are one.

Three kinds, decided by who the channel is about:

``DIRECT``  one member and the pod: their private chats on every platform.
``CONTACT`` one contact and the pod: their private chats, web chat, emails.
``GROUP``   a chat several people read: a group, a shared channel, a thread
            with other people on it.

An agent's working -- its tool calls and reasoning -- is never a channel
message. That stays in the conversation, which is one agent working for one
person, attached to the channel.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator


class ChannelKind(StrEnum):
    DIRECT = "DIRECT"
    CONTACT = "CONTACT"
    GROUP = "GROUP"


class SourceKind(StrEnum):
    """Whether a source is read by one person outside the pod or by several."""

    #: A private chat, a web chat, an email between two people.
    PRIVATE = "PRIVATE"
    #: A group, a shared channel, an email thread with others on it.
    SHARED = "SHARED"


class SenderKind(StrEnum):
    MEMBER = "MEMBER"
    CONTACT = "CONTACT"
    AGENT = "AGENT"


class Principal(BaseModel):
    """A person: a member of the pod, or one of its contacts. Exactly one."""

    model_config = ConfigDict(frozen=True)

    user_id: UUID | None = None
    contact_id: UUID | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.user_id is None) == (self.contact_id is None):
            raise ValueError("A principal is a member or a contact, exactly one")
        return self

    @classmethod
    def member(cls, user_id: UUID) -> Principal:
        return cls(user_id=user_id)

    @classmethod
    def contact(cls, contact_id: UUID) -> Principal:
        return cls(contact_id=contact_id)

    @property
    def is_contact(self) -> bool:
        return self.contact_id is not None


class Channel(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    pod_id: UUID
    kind: ChannelKind
    title: str | None = None
    #: Whose DIRECT channel this is.
    member_user_id: UUID | None = None
    #: Whose CONTACT channel this is.
    contact_id: UUID | None = None
    #: A member who stepped in; while one has, agents do not answer here.
    handler_user_id: UUID | None = None
    created_at: datetime
    last_message_at: datetime | None = None


class Source(BaseModel):
    """One outside place a channel is fed by.

    Keyed by the surface it arrives on and the place on the platform: a group's
    or a thread's id, or -- for a private chat -- the person's id on the
    platform, so a chat that moves (a WhatsApp number reassigned in the pool)
    stays the same source.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    channel_id: UUID
    pod_id: UUID
    surface_id: UUID | None
    platform: str
    kind: SourceKind
    external_channel_id: str | None = None
    external_user_id: str | None = None
    title: str | None = None
    last_inbound_at: datetime | None = None
    #: What the surface last heard here: how to write into the place cold.
    delivery_ref: dict[str, object] | None = None
    # -- a shared place the pod's bot is in -----------------------------------
    owner_user_id: UUID | None = None
    answers_outsiders: bool = True
    shared_externally: bool = False
    invite_link: str | None = None
    request_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_pending(self) -> bool:
        """A group the bot asked a platform to create, not confirmed yet."""
        return self.external_channel_id is None and self.external_user_id is None

    @property
    def welcomes_outsiders(self) -> bool:
        """Whether a stranger's question here gets an answer: the pod wants it,
        and somebody answers for it."""
        return self.answers_outsiders and self.owner_user_id is not None


class Participant(BaseModel):
    model_config = ConfigDict(frozen=True)

    channel_id: UUID
    principal: Principal
    last_read_at: datetime | None = None


class Sender(BaseModel):
    """Who said a line, and -- for an agent -- whom it said it for."""

    model_config = ConfigDict(frozen=True)

    kind: SenderKind
    user_id: UUID | None = None
    contact_id: UUID | None = None
    agent_id: UUID | None = None
    #: For an agent: the person it acted for, whose access it used.
    on_behalf_of: Principal | None = None
    #: What to call them on the timeline, as the platform named them.
    display_name: str | None = None

    @model_validator(mode="after")
    def _one_sender(self) -> Self:
        """Names nobody but its own kind -- and, once that person or agent is
        gone, nobody at all: the line stays, said by somebody since removed."""
        named = {
            SenderKind.MEMBER: self.user_id,
            SenderKind.CONTACT: self.contact_id,
            SenderKind.AGENT: self.agent_id,
        }
        if any(value is not None for kind, value in named.items() if kind != self.kind):
            raise ValueError(f"A {self.kind.value} sender names only one")
        if self.kind is not SenderKind.AGENT and self.on_behalf_of is not None:
            raise ValueError("Only an agent acts on somebody's behalf")
        return self

    @classmethod
    def member(cls, user_id: UUID, *, display_name: str | None = None) -> Sender:
        return cls(kind=SenderKind.MEMBER, user_id=user_id, display_name=display_name)

    @classmethod
    def contact(cls, contact_id: UUID, *, display_name: str | None = None) -> Sender:
        return cls(
            kind=SenderKind.CONTACT, contact_id=contact_id, display_name=display_name
        )

    @classmethod
    def agent(
        cls,
        agent_id: UUID,
        *,
        on_behalf_of: Principal,
        display_name: str | None = None,
    ) -> Sender:
        return cls(
            kind=SenderKind.AGENT,
            agent_id=agent_id,
            on_behalf_of=on_behalf_of,
            display_name=display_name,
        )

    @property
    def used_a_members_access(self) -> bool:
        """An agent's line written with one member's own access.

        One whose principal is no longer known counts: it fails closed, so no
        contact reads an answer nobody can say was made for them.
        """
        return self.kind is SenderKind.AGENT and (
            self.on_behalf_of is None or not self.on_behalf_of.is_contact
        )


class ChannelMessage(BaseModel):
    """One line of a channel's timeline."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    channel_id: UUID
    sender: Sender
    text: str
    created_at: datetime
    #: Where it was said, or ``None`` for a line written in Lemma.
    source_id: UUID | None = None
    #: A note for the pod's members only: never delivered, never shown outside.
    members_only: bool = False
    conversation_id: UUID | None = None
    external_message_id: str | None = None
    external_thread_id: str | None = None
    #: On a line a person said on a platform: what its surface needs to answer
    #: it in place.
    reply_ref: dict[str, object] | None = None
