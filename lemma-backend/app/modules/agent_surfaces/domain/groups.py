"""A group chat a pod's bot is in, and the member who answers for it.

A Telegram group, a WhatsApp group, a Slack channel: several people, one of
them the pod's bot. Members of the pod talk to the bot there exactly as they do
in private -- each turn runs as whoever asked, in their own conversation. What
a group adds is everybody else: a vendor, a client, a colleague from another
team. They are not in the pod and hold no access to it, and the bot answers
them anyway, *for the pod* -- reading only what the pod has marked Public (see
``app.modules.agent.domain.outsiders``).

Somebody has to answer for that, and it is the group's ``owner_user_id``: the
member who brought the bot into the group, or who switched it on for outsiders
since. Their conversation is where outsiders' questions land, where the bot
passes on what it cannot answer, and whose name is on it. With no owner there is
nobody to answer for a stranger's turn, so outsiders are not answered at all --
members still are.

A group is a shared place of a GROUP channel (``app.modules.channels``): the
place's row (``Source``) keeps the group's settings, and its id is the group's.
There is no second type for it here: a surface reads and writes a group through
``channels.contracts.places``. What is
said in it is the channel's timeline. Each member's turn runs in their own
conversation, so without it the bot would know only what was said to it; on
platforms whose history cannot be fetched -- Telegram, WhatsApp -- it is the
only record there is.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel

from app.modules.agent_surfaces.domain.external_accounts import account_of
from app.modules.channels.contracts import Place, Source, SourceKind

if TYPE_CHECKING:
    from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity


def group_place(
    surface: AgentSurfaceEntity,
    *,
    chat: str | None,
    title: str | None,
    is_private: bool = True,
) -> Place:
    """A group chat as the surface that is in it names it: a shared place of
    its account. ``chat`` is None for a group still being made."""
    return Place(
        surface_id=surface.id,
        platform=surface.surface_type.value,
        account=account_of(surface),
        kind=SourceKind.CHAT,
        external_channel_id=chat,
        external_user_id=None,
        title=title,
        is_private=is_private,
    )


def as_group(source: Source | None) -> Source | None:
    """``source`` if it is a group a surface is in; a person's place is not."""
    if (
        source is None
        or source.kind is not SourceKind.CHAT
        or source.surface_id is None
    ):
        return None
    return source


class GroupUpdateKind(StrEnum):
    """What the platform says happened to a group the bot asked for or is in."""

    #: A creation the bot asked for succeeded; the group now has an id.
    CREATED = "CREATED"
    #: A creation the bot asked for was refused.
    CREATE_FAILED = "CREATE_FAILED"
    #: The group is gone; nobody can speak in it again.
    DELETED = "DELETED"
    #: The platform now keeps the place to its members (a Slack channel
    #: converted to private). Only ever this way round.
    BECAME_PRIVATE = "BECAME_PRIVATE"
    #: Shared with another company, or no longer (Slack Connect).
    SHARED = "SHARED"
    UNSHARED = "UNSHARED"
    #: Somebody left, or was taken out of, the place (``external_user_id``).
    MEMBER_LEFT = "MEMBER_LEFT"
    #: Somebody joined the place, or was added to it (``external_user_id``).
    MEMBER_JOINED = "MEMBER_JOINED"
    #: The bot itself was taken out of the place, or put back in it.
    BOT_LEFT = "BOT_LEFT"
    BOT_RETURNED = "BOT_RETURNED"


class ParsedGroupUpdate(BaseModel):
    """A platform's word about a group itself, as opposed to a message in it.

    WhatsApp confirms a group the bot asked it to create, naming the
    ``request_id`` it answers. Every platform says who joined a place and who
    left it; Slack, Teams and Telegram say when the bot was taken out of one or
    put back, and Slack when a channel turned private or was shared with
    another company.
    """

    platform: str
    kind: GroupUpdateKind
    request_id: str | None = None
    external_channel_id: str | None = None
    title: str | None = None
    invite_link: str | None = None
    #: The business number the group belongs to, to fetch its link through.
    phone_number_id: str | None = None
    #: The workspace or tenant the place is in, where the platform has one:
    #: only surfaces installed there are asked about it.
    tenant_id: str | None = None
    #: Who left (``MEMBER_LEFT``) or joined (``MEMBER_JOINED``), as the
    #: platform names them -- a Teams person by their AAD object id where the
    #: event carries it.
    external_user_id: str | None = None
    #: The name the platform shows a person joining under, where it shows one.
    display_name: str | None = None
    #: A WhatsApp person's number, international, where the event names it.
    phone: str | None = None
