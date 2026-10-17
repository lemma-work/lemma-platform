"""The wire shapes of a pod's channels, and the mappers to them."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Participant,
    SenderKind,
    Source,
    SourceKind,
)


class ChannelSourceResponse(BaseModel):
    id: UUID
    platform: str = Field(
        description="Where it is: TELEGRAM, WHATSAPP, SLACK, RESEND, WEB…"
    )
    kind: SourceKind
    title: str | None = None
    surface_id: UUID | None = None
    last_inbound_at: datetime | None = None


class ChannelParticipantResponse(BaseModel):
    user_id: UUID | None = Field(default=None, description="A member of the pod.")
    contact_id: UUID | None = Field(default=None, description="One of its contacts.")
    last_read_at: datetime | None = None


class ChannelResponse(BaseModel):
    id: UUID
    kind: ChannelKind
    title: str | None = None
    member_user_id: UUID | None = Field(
        default=None, description="Whose direct channel this is."
    )
    contact_id: UUID | None = Field(
        default=None, description="Which contact's channel this is."
    )
    handler_user_id: UUID | None = Field(
        default=None,
        description="A member answering by hand; agents do not answer meanwhile.",
    )
    created_at: datetime
    last_message_at: datetime | None = None


class ChannelDetailResponse(ChannelResponse):
    sources: list[ChannelSourceResponse] = Field(default_factory=list)
    participants: list[ChannelParticipantResponse] = Field(default_factory=list)


class ChannelListResponse(BaseModel):
    items: list[ChannelResponse]
    next_before: str | None = Field(
        default=None, description="Pass as `before` for the next page."
    )


class ChannelMessageResponse(BaseModel):
    id: UUID
    sender_kind: SenderKind
    sender_user_id: UUID | None = None
    sender_contact_id: UUID | None = None
    sender_agent_id: UUID | None = None
    sender_name: str | None = None
    on_behalf_user_id: UUID | None = Field(
        default=None, description="For an agent's line: the member it acted for."
    )
    on_behalf_contact_id: UUID | None = Field(
        default=None, description="For an agent's line: the contact it answered."
    )
    text: str
    source_id: UUID | None = Field(
        default=None,
        description="Where it was said; absent for a line written in Lemma.",
    )
    members_only: bool = False
    created_at: datetime


class ChannelMessagesResponse(BaseModel):
    items: list[ChannelMessageResponse] = Field(
        description="Oldest first, the newest page unless `before` was given."
    )
    next_before: str | None = Field(
        default=None, description="Pass as `before` for the page before this one."
    )


def channel_response(channel: Channel) -> ChannelResponse:
    return ChannelResponse(
        id=channel.id,
        kind=channel.kind,
        title=channel.title,
        member_user_id=channel.member_user_id,
        contact_id=channel.contact_id,
        handler_user_id=channel.handler_user_id,
        created_at=channel.created_at,
        last_message_at=channel.last_message_at,
    )


def detail_response(
    channel: Channel, sources: list[Source], participants: list[Participant]
) -> ChannelDetailResponse:
    return ChannelDetailResponse(
        **channel_response(channel).model_dump(),
        sources=[
            ChannelSourceResponse(
                id=source.id,
                platform=source.platform,
                kind=source.kind,
                title=source.title,
                surface_id=source.surface_id,
                last_inbound_at=source.last_inbound_at,
            )
            for source in sources
        ],
        participants=[
            ChannelParticipantResponse(
                user_id=participant.principal.user_id,
                contact_id=participant.principal.contact_id,
                last_read_at=participant.last_read_at,
            )
            for participant in participants
        ],
    )


def message_response(message: ChannelMessage) -> ChannelMessageResponse:
    sender = message.sender
    behalf = sender.on_behalf_of
    return ChannelMessageResponse(
        id=message.id,
        sender_kind=sender.kind,
        sender_user_id=sender.user_id,
        sender_contact_id=sender.contact_id,
        sender_agent_id=sender.agent_id,
        sender_name=sender.display_name,
        on_behalf_user_id=behalf.user_id if behalf else None,
        on_behalf_contact_id=behalf.contact_id if behalf else None,
        text=message.text,
        source_id=message.source_id,
        members_only=message.members_only,
        created_at=message.created_at,
    )
