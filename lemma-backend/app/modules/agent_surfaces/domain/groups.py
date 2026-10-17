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
place's row keeps the group's settings, and its id is the group's. What is
said in it is the channel's timeline. Each member's turn runs in their own
conversation, so without it the bot would know only what was said to it; on
platforms whose history cannot be fetched -- Telegram, WhatsApp -- it is the
only record there is.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel

from app.core.domain.entity import Entity
from app.modules.channels.contracts import Source


class SurfaceGroup(Entity):
    """One group, on one surface: a shared place, as the surface reads it."""

    #: The group's channel.
    channel_id: UUID
    pod_id: UUID
    surface_id: UUID
    platform: str
    #: None only while a group the bot asked the platform to create is still
    #: being made (WhatsApp creates groups asynchronously; see ``request_id``).
    external_channel_id: str | None = None
    title: str | None = None
    owner_user_id: UUID | None = None
    answers_outsiders: bool = True
    #: The platform's id for a creation still in flight, which the confirmation
    #: echoes back. Kept once the group exists, to recognise a replayed one.
    request_id: str | None = None
    #: The link people join by, where joining is by link (WhatsApp).
    invite_link: str | None = None
    #: A Slack channel shared with another company. There, and only there, a
    #: Slack sender outside the pod is an outsider to answer; inside one's own
    #: workspace they are a colleague to invite.
    shared_externally: bool = False
    #: When somebody last said something in it.
    last_inbound_at: datetime | None = None

    @property
    def is_pending(self) -> bool:
        """Asked for, and not yet confirmed by the platform."""
        return self.external_channel_id is None

    @property
    def welcomes_outsiders(self) -> bool:
        """Whether a stranger's question here gets an answer.

        Both halves, because either alone is not enough: the switch says the
        pod wants it, the owner is who answers for it.
        """
        return self.answers_outsiders and self.owner_user_id is not None

    @classmethod
    def of(cls, source: Source) -> SurfaceGroup:
        """The group a shared place is. Only a place on a surface is one."""
        if source.surface_id is None:
            raise ValueError("A group is a place on a surface")
        return cls(
            id=source.id,
            channel_id=source.channel_id,
            pod_id=source.pod_id,
            surface_id=source.surface_id,
            platform=source.platform,
            external_channel_id=source.external_channel_id,
            title=source.title,
            owner_user_id=source.owner_user_id,
            answers_outsiders=source.answers_outsiders,
            request_id=source.request_id,
            invite_link=source.invite_link,
            shared_externally=source.shared_externally,
            last_inbound_at=source.last_inbound_at,
            created_at=source.created_at or datetime.now(timezone.utc),
            updated_at=source.updated_at or datetime.now(timezone.utc),
        )


class GroupUpdateKind(StrEnum):
    """What the platform says happened to a group the bot asked for or is in."""

    #: A creation the bot asked for succeeded; the group now has an id.
    CREATED = "CREATED"
    #: A creation the bot asked for was refused.
    CREATE_FAILED = "CREATE_FAILED"
    #: The group is gone; nobody can speak in it again.
    DELETED = "DELETED"


class ParsedGroupUpdate(BaseModel):
    """A platform's word about a group itself, as opposed to a message in it.

    Only WhatsApp sends these today, because only there does the bot create
    groups: the creation is confirmed later, naming the ``request_id`` it
    answers, and only then does the group have an id to be spoken in.
    """

    platform: str
    kind: GroupUpdateKind
    request_id: str | None = None
    external_channel_id: str | None = None
    title: str | None = None
    invite_link: str | None = None
    #: The business number the group belongs to, to fetch its link through.
    phone_number_id: str | None = None
