"""What the Groups API reads and writes, and how a group summary is shown."""

from __future__ import annotations

from app.modules.agent_surfaces.api import group_public as public_api

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.agent_surfaces.platforms.whatsapp.client import (
    GROUP_SUBJECT_MAX_CHARS,
)
from app.modules.agent_surfaces.services.space_groups import (
    GroupDetail,
    GroupSummary,
)


class GroupOwnerResponse(BaseModel):
    user_id: UUID
    display_name: str | None = None


class GroupResponse(BaseModel):
    id: UUID
    surface_name: str
    platform: str
    title: str | None = None
    external_channel_id: str | None = None
    invite_link: str | None = None
    pending: bool = Field(
        default=False,
        description="Asked of the platform and not yet confirmed (WhatsApp).",
    )
    shared_externally: bool = Field(
        default=False,
        description="A Slack channel shared with another company.",
    )
    owner: GroupOwnerResponse | None = Field(
        default=None, description="Who answers for the people outside the pod."
    )
    answers_outsiders: bool
    welcomes_outsiders: bool = Field(
        description=(
            "People outside the pod are answered here today: the group's switch "
            "is on, a member of the pod answers for them, and the bot's own "
            "switch is on."
        )
    )
    bot_answers_outsiders: bool = Field(
        default=True,
        description=(
            "The bot's own switch, over every group it is in. Off, nobody outside "
            "the pod is answered in any of them."
        ),
    )
    can_manage: bool = Field(
        default=False,
        description=(
            "The reader may switch outsiders for this group or take it over: "
            "they answer for it, nobody in the pod does, or they are an admin "
            "of the pod."
        ),
    )
    people_in_pod: int | None = Field(
        default=None,
        description="People in the pod seen speaking here; none where not kept.",
    )
    people_outside: int | None = Field(
        default=None,
        description="People outside the pod seen speaking here.",
    )
    last_message_at: datetime | None = None
    waiting_for_you: int = Field(
        default=0,
        description="Questions its people outside the pod passed on to you.",
    )
    updated_at: datetime


class GroupPersonResponse(BaseModel):
    name: str
    external_id: str | None = Field(
        default=None,
        description=(
            "Who they are to the pod -- ``member:<id>`` or ``contact:<id>`` -- "
            "the key a timeline line's ``author_external_id`` matches."
        ),
    )
    user_id: UUID | None = None
    in_pod: bool


class GroupWaitingResponse(BaseModel):
    notification_id: UUID = Field(
        description="Answer it with the notification's respond endpoint."
    )
    question: str
    asked_at: datetime


class GroupDetailResponse(GroupResponse):
    people: list[GroupPersonResponse]
    waiting: list[GroupWaitingResponse]
    public: public_api.GroupPublicResponse | None = None


class GroupListResponse(BaseModel):
    items: list[GroupResponse]


class GroupLineResponse(BaseModel):
    author_name: str | None = None
    author_external_id: str | None = Field(
        default=None, description="The speaker's key among the group's people."
    )
    in_pod: bool
    from_bot: bool
    text: str | None = Field(
        default=None, description="None where the line is withheld from the reader."
    )
    withheld: bool = Field(
        default=False,
        description=(
            "An answer the bot made with another member's own access: shown to "
            "that member alone. ``answered_name`` still says whom it was for."
        ),
    )
    at: datetime
    answered_name: str | None = Field(
        default=None, description="On the bot's lines: whom it answered."
    )
    answered_from_public: bool = Field(
        default=False,
        description="On the bot's lines: answered from what the pod made Public.",
    )


class GroupTimelineResponse(BaseModel):
    items: list[GroupLineResponse]


class GroupStartRequest(BaseModel):
    surface_name: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=GROUP_SUBJECT_MAX_CHARS)
    answers_outsiders: bool = True


class GroupUpdateRequest(BaseModel):
    answers_outsiders: bool | None = Field(
        default=None,
        description=(
            "Answer people outside the pod in this group. Switching it on where "
            "nobody answers for them makes the caller the one who does."
        ),
    )
    take_over: bool = Field(
        default=False,
        description="The caller answers for this group's outsiders from now on.",
    )


class GroupLinkRequest(BaseModel):
    surface_name: str = Field(min_length=1)


class GroupLinkResponse(BaseModel):
    url: str = Field(description="Opens Telegram's own choose-a-group screen.")
    expires_at: datetime


def _response(summary: GroupSummary, *, can_manage: bool = False) -> GroupResponse:
    group = summary.group
    # A member who has left the pod answers for nobody, and reads as nobody.
    owner = (
        GroupOwnerResponse(user_id=group.owner_user_id, display_name=summary.owner_name)
        if group.owner_user_id is not None and summary.owner_in_pod
        else None
    )
    return GroupResponse(
        id=group.id,
        surface_name=summary.surface_name,
        platform=group.platform,
        title=group.title,
        external_channel_id=group.external_channel_id,
        invite_link=group.invite_link,
        pending=group.is_pending,
        shared_externally=group.shared_externally,
        owner=owner,
        answers_outsiders=group.answers_outsiders,
        welcomes_outsiders=summary.answers_outsiders_now,
        bot_answers_outsiders=summary.bot_answers_outsiders,
        can_manage=can_manage,
        people_in_pod=summary.members,
        people_outside=summary.outsiders,
        last_message_at=summary.last_message_at,
        waiting_for_you=summary.waiting_for_viewer,
        updated_at=group.updated_at,
    )


def _detail_response(
    detail: GroupDetail, *, can_manage: bool = False
) -> GroupDetailResponse:
    return GroupDetailResponse(
        **_response(detail, can_manage=can_manage).model_dump(),
        people=[GroupPersonResponse(**p.model_dump()) for p in detail.people],
        waiting=[GroupWaitingResponse(**w.model_dump()) for w in detail.waiting],
    )
