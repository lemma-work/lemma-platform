"""What a group source's routes read and write: its settings.

A group's timeline, people and unread lines are its channel's
(``/pods/{pod_id}/channels``); this is only what the bot's side of it keeps.
The settings' shape is ``channels``' (``contracts.source_settings``), since the
channel list shows it too.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.modules.agent_surfaces.api import group_public as public_api
from app.modules.agent_surfaces.platforms.whatsapp.client import (
    GROUP_SUBJECT_MAX_CHARS,
)
from app.modules.agent_surfaces.services.source_settings import SourceState
from app.modules.channels.contracts.people import PersonRef, SenderKind
from app.modules.channels.contracts.source_settings import SourceSettingsResponse


class SourceSettingsDetailResponse(SourceSettingsResponse):
    public: public_api.GroupPublicResponse | None = Field(
        default=None, description="What people outside the pod can be answered from."
    )


class WhatsAppGroupRequest(BaseModel):
    surface_name: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=GROUP_SUBJECT_MAX_CHARS)
    answers_outsiders: bool = True


class SourceUpdateRequest(BaseModel):
    """Absent is unchanged; neither field can be cleared."""

    answers_outsiders: bool | None = Field(
        default=None,
        description="Answer people outside the pod here, or stop.",
    )
    owner: Literal["me"] | None = Field(
        default=None,
        description="`me`: answer for its outsiders yourself from now on.",
    )


class TelegramLinkRequest(BaseModel):
    surface_name: str = Field(min_length=1)


class TelegramLinkResponse(BaseModel):
    url: str = Field(description="Opens Telegram's own choose-a-group screen.")
    expires_at: datetime


def settings_response(
    state: SourceState, *, can_manage: bool
) -> SourceSettingsResponse:
    group = state.group
    # A member who has left the pod answers for nobody, and reads as nobody.
    owner = (
        PersonRef(kind=SenderKind.MEMBER, id=group.owner_user_id, name=state.owner_name)
        if group.owner_user_id is not None and state.owner_in_pod
        else None
    )
    return SourceSettingsResponse(
        id=group.id,
        channel_id=group.channel_id,
        surface_name=state.surface_name,
        platform=group.platform,
        title=group.title,
        invite_link=group.invite_link,
        pending=group.is_pending,
        shared_externally=group.shared_externally,
        owner=owner,
        answers_outsiders=group.answers_outsiders,
        welcomes_outsiders=state.answers_outsiders_now,
        bot_answers_outsiders=state.bot_answers_outsiders,
        can_manage=can_manage,
        updated_at=group.updated_at,
    )


def detail_response(
    state: SourceState,
    *,
    can_manage: bool,
    public: public_api.GroupPublicResponse | None,
) -> SourceSettingsDetailResponse:
    return SourceSettingsDetailResponse(
        **settings_response(state, can_manage=can_manage).model_dump(), public=public
    )
