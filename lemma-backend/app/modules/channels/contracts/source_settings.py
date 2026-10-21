"""A group source's settings, listed with the channels a bot is in.

A WhatsApp or Telegram group, a Slack or Teams channel: each is a source of
one of the pod's GROUP channels. What the bot's side keeps about it -- whether
people outside the pod are answered there, and which member answers for them
-- is ``agent_surfaces``'; the channel list (``GET /channels?source_surface=``)
shows it beside each channel, so the bot's settings page reads one list.

Channels does not know bots. ``agent_surfaces`` registers the one lister
(``lists_source_settings``) when its API is wired; the list asks it for the
named bot's sources the reader may see the settings of -- refusing, as its
own routes do, a reader who may not read the bot.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.contracts.people import PersonRef

__all__ = [
    "SourceSettingsLister",
    "SourceSettingsQuery",
    "SourceSettingsResponse",
    "lists_source_settings",
    "source_settings_lister",
]


class SourceSettingsResponse(BaseModel):
    id: UUID = Field(description="The source's id, as the channel lists it.")
    channel_id: UUID
    surface_name: str
    platform: str
    title: str | None = None
    invite_link: str | None = None
    pending: bool = Field(
        default=False,
        description="Asked of the platform and not yet confirmed (WhatsApp).",
    )
    shared_externally: bool = Field(
        default=False,
        description="A Slack channel shared with another company.",
    )
    owner: PersonRef | None = Field(
        default=None, description="The member who answers for its outsiders."
    )
    answers_outsiders: bool = Field(
        description="This group's own switch for people outside the pod."
    )
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
            "The reader may switch outsiders here or take it over: they answer "
            "for it, nobody in the pod does, or they are an admin of the pod."
        ),
    )
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SourceSettingsQuery:
    uow: SqlAlchemyUnitOfWork
    ctx: Context
    pod_id: UUID
    viewer_id: UUID
    surface_name: str


#: The named bot's group sources, each with its settings as the reader sees
#: them. Raises the bot's own refusal (404 for no such bot, 403 for one the
#: reader may not read).
SourceSettingsLister = Callable[
    [SourceSettingsQuery], Awaitable[list[SourceSettingsResponse]]
]

_LISTERS: list[SourceSettingsLister] = []  # memory: bounded, one set at import


def lists_source_settings(lister: SourceSettingsLister) -> SourceSettingsLister:
    """Register the one lister of sources' settings. Once."""
    if _LISTERS and _LISTERS[0] is not lister:
        raise ValueError("Source settings already have a lister")
    _LISTERS[:] = [lister]
    return lister


def source_settings_lister() -> SourceSettingsLister | None:
    return _LISTERS[0] if _LISTERS else None
