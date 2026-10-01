"""A group chat a pod's bot is in, and the member who answers for it.

A Telegram group, a WhatsApp group, a Slack channel: several people, one of
them the pod's bot. Members of the pod talk to the bot there exactly as they do
in private -- each turn runs as whoever asked, in their own conversation. What
a group adds is everybody else: a vendor, a client, a colleague from another
team. They are not in the pod and hold no access to it, and the bot answers
them anyway, *for the pod* -- reading only what the pod has marked Public (see
``app.modules.agent.domain.outsiders``).

Somebody has to answer for that, and it is this row's ``owner_user_id``: the
member who brought the bot into the group, or who switched it on for outsiders
since. Their conversation is where outsiders' questions land, where the bot
passes on what it cannot answer, and whose name is on it. With no owner there is
nobody to answer for a stranger's turn, so outsiders are not answered at all --
members still are.

The log beside it (``GroupLine``) is what the bot has heard in the group. Each
member's turn runs in their own conversation, so without it the bot would know
only what was said to it; on platforms whose history cannot be fetched --
Telegram, WhatsApp -- it is the only record there is.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel

from app.core.domain.entity import Entity

#: The link-key user for a group's people from outside the pod. Every link is
#: keyed by sender so that each member has their own conversation; outsiders
#: share one per group, owned by the member who answers for it, so the owner's
#: history is one thread per group rather than one per stranger. Not a platform
#: user id on any platform: none of them starts with "~".
OUTSIDERS_LINK_USER = "~outsiders"

#: How long the pod keeps what was said in a group. A group's page and the
#: bot's background read only the recent past, and the people in a group --
#: some of them strangers -- are told the log exists and how long it lasts.
GROUP_LOG_RETENTION = timedelta(days=90)


class SurfaceGroup(Entity):
    """One group, on one surface."""

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


class GroupLine(BaseModel):
    """One thing said in a group, as the pod's log recorded it."""

    author_external_id: str | None = None
    author_name: str | None = None
    #: The Lemma user the author proved to be, when they have; None for a
    #: stranger and for the bot itself.
    author_user_id: UUID | None = None
    from_agent: bool = False
    text: str
    created_at: datetime
    #: On a line the bot wrote: whom it answered, and whether from what is
    #: Public (somebody outside the pod) or with the asker's own access --
    #: whose, in ``answered_user_id``, the one member a group's page shows
    #: that answer to.
    answered_name: str | None = None
    answered_from_public: bool = False
    answered_user_id: UUID | None = None


def answer_withheld_from(line: GroupLine, viewer_id: UUID | None) -> bool:
    """Whether this line is an answer made with somebody else's access.

    An answer from what is Public is anybody's to read; so is anything a person
    said. An answer the bot made with a member's own access is that member's --
    withheld from every other member on the group's page, and from a stranger's
    run (``viewer_id`` None) in the background it is handed.
    """
    if not line.from_agent or line.answered_from_public:
        return False
    return viewer_id is None or line.answered_user_id != viewer_id


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
