"""Typed surface event metadata models.

Each platform defines what per-message context is available to the agent's
tools at runtime (file attachments, thread state, channel identifiers, etc.).

These are created from the raw ParsedInboundSurfaceEvent.metadata dict in the
ingress service and injected directly into ConversationContext via chat_stream().
They are NEVER stored in the DB — they are per-invocation runtime context only.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

from app.modules.agent_surfaces.platforms.email_models import (
    EmailFileAttachment,
    GmailFileAttachment,
    OutlookFileAttachment,
)
from app.modules.agent_surfaces.platforms.slack.models import SlackFileAttachment
from app.modules.agent_surfaces.platforms.teams.models import TeamsFileAttachment
from app.modules.agent_surfaces.platforms.common import SurfaceFileAttachment


class WhatsAppFileAttachment(SurfaceFileAttachment):
    pass


class TelegramFileAttachment(SurfaceFileAttachment):
    file_id: str | None = None


class TeamsSurfaceEventMetadata(BaseModel):
    platform: Literal["TEAMS"] = "TEAMS"
    team_id: str | None = None
    team_aad_group_id: str | None = None
    channel_id: str | None = None
    service_url: str | None = None
    conversation_id: str | None = None
    reply_to_id: str | None = None
    is_thread_reply: bool = False
    attachments: list[TeamsFileAttachment] = Field(default_factory=list)


class SlackSurfaceEventMetadata(BaseModel):
    platform: Literal["SLACK"] = "SLACK"
    is_thread_reply: bool = False
    mentioned_user_ids: list[str] = Field(default_factory=list)
    attachments: list[SlackFileAttachment] = Field(default_factory=list)


class WhatsAppSurfaceEventMetadata(BaseModel):
    platform: Literal["WHATSAPP"] = "WHATSAPP"
    waba_id: str | None = None
    phone_number_id: str | None = None
    contacts: list[dict] = Field(default_factory=list)
    attachments: list[WhatsAppFileAttachment] = Field(default_factory=list)


class TelegramSurfaceEventMetadata(BaseModel):
    platform: Literal["TELEGRAM"] = "TELEGRAM"
    chat_type: str | None = None
    chat_id: str | None = None
    is_topic_message: bool = False
    message_thread_id: str | None = None
    attachments: list[TelegramFileAttachment] = Field(default_factory=list)


class GmailSurfaceEventMetadata(BaseModel):
    platform: Literal["GMAIL"] = "GMAIL"
    mailbox_email: str | None = None
    subject: str | None = None
    thread_id: str | None = None
    message_id: str | None = None
    reply_to_email: str | None = None
    references: list[str] = Field(default_factory=list)
    in_reply_to: str | None = None
    attachments: list[GmailFileAttachment] = Field(default_factory=list)


class OutlookSurfaceEventMetadata(BaseModel):
    platform: Literal["OUTLOOK"] = "OUTLOOK"
    mailbox_email: str | None = None
    subject: str | None = None
    thread_id: str | None = None
    message_id: str | None = None
    internet_message_id: str | None = None
    reply_to_email: str | None = None
    references: list[str] = Field(default_factory=list)
    in_reply_to: str | None = None
    attachments: list[OutlookFileAttachment] = Field(default_factory=list)


class ResendSurfaceEventMetadata(BaseModel):
    platform: Literal["RESEND"] = "RESEND"
    mailbox_email: str | None = None
    subject: str | None = None
    thread_id: str | None = None
    message_id: str | None = None
    reply_to_email: str | None = None
    references: list[str] = Field(default_factory=list)
    in_reply_to: str | None = None
    attachments: list[EmailFileAttachment] = Field(default_factory=list)


SurfaceEventMetadata = Annotated[
    Union[
        TeamsSurfaceEventMetadata,
        SlackSurfaceEventMetadata,
        WhatsAppSurfaceEventMetadata,
        TelegramSurfaceEventMetadata,
        GmailSurfaceEventMetadata,
        OutlookSurfaceEventMetadata,
        ResendSurfaceEventMetadata,
    ],
    Field(discriminator="platform"),
]

_METADATA_ADAPTER: TypeAdapter[SurfaceEventMetadata] = TypeAdapter(SurfaceEventMetadata)


def build_surface_event_metadata(
    platform: str,
    raw: dict,
) -> SurfaceEventMetadata | None:
    """Parse a raw event metadata dict into the platform's typed model.

    Unknown keys are dropped; attachments without any identifying field
    (id/name/url/inline content) are filtered out.
    """
    normalized = str(platform).upper()
    try:
        metadata = _METADATA_ADAPTER.validate_python({**raw, "platform": normalized})
    except Exception:
        return None
    metadata.attachments = [
        attachment
        for attachment in metadata.attachments
        if _attachment_is_identifiable(attachment)
    ]
    return metadata


def _attachment_is_identifiable(attachment: SurfaceFileAttachment) -> bool:
    return bool(
        attachment.id
        or attachment.name
        or attachment.download_url
        or getattr(attachment, "file_id", None)
        or getattr(attachment, "content_bytes_base64", None)
    )
