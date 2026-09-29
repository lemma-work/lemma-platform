"""Wire shapes for the user-scoped ``/surfaces/me`` routes.

Kept beside the pod-scoped schemas rather than inside them: these answer for a
person across every pod they belong to, which is a different question from what
one pod has configured.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.modules.agent_surfaces.domain.entities import SurfacePlatform


class UserSurfaceItem(BaseModel):
    """One of the current user's surfaces (across any pod they belong to)."""

    id: UUID
    name: str
    pod_id: UUID
    platform: SurfacePlatform
    agent_id: UUID | None = None
    is_default: bool = False
    # True when another surface in this list answers at the same address (the
    # deployment's shared bot/number). Only these are a choice; a pod's own bot
    # has its own handle, so a message to it can only arrive there.
    shares_address: bool = False


class UserSurfacePlatformGroup(BaseModel):
    """All of a user's surfaces for one platform. ``conflict`` is true when two
    of them answer at the same address, so the user has to say which pod hears
    them (the ``shares_address`` surfaces are the ones to choose between)."""

    platform: SurfacePlatform
    conflict: bool = False
    default_surface_id: UUID | None = None
    surfaces: list[UserSurfaceItem]


class UserSurfacesResponse(BaseModel):
    groups: list[UserSurfacePlatformGroup]


class TelegramLinkPod(BaseModel):
    id: UUID
    name: str


class TelegramLinkOptionsResponse(BaseModel):
    """What a link to the shared Telegram bot would connect, before minting one."""

    bot_username: str
    #: Every pod the chat could answer from.
    pods: list[TelegramLinkPod]
    #: The pod it answers from when none is chosen: the one this person's
    #: Telegram already reaches, or else the first listed. None with no pods,
    #: when redeeming the link makes them a workspace as signup would.
    pod_id: UUID | None = None


class TelegramLinkRequest(BaseModel):
    pod_id: UUID | None = None


class TelegramLinkResponse(BaseModel):
    """A one-time link that connects whoever opens it in Telegram to this user.

    Single use and short-lived; open it straight away rather than sharing it.
    """

    url: str
    bot_username: str
    expires_at: datetime
    pod_id: UUID | None = None


class SetDefaultSurfaceRequest(BaseModel):
    """Pick which surface answers this user for ``platform`` when several could."""

    platform: SurfacePlatform
    surface_id: UUID
