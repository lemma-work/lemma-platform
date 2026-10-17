"""A pod's groups, as the people in the pod see them.

The chats on WhatsApp, Telegram and Slack that the pod's bot is in, gathered in
one place: what each is called, who in it is in the pod and who is not, who
answers for the ones who are not, and what is waiting on the person looking.

Everyone in the pod may read a group's page -- it is the pod's group, and its
bot speaks for the pod there. Two things never appear on it. Anybody's private
asking: a member's own conversation stays theirs, and a note written in Lemma
is never a line of the group. And what the bot told one member with that
member's own access: the page says the answer was given, and to whom, and shows
its words to that member alone, because the reader may not be able to see what
it was made from.

Everything here is a bounded read over rows other modules already keep: the
group registry (``group_repository``), the group's channel -- its timeline and
who has spoken in it (``app.modules.channels``) -- the thread links that name
a group's contacts' conversations, and the notifications those sent the member
who answers for them. The list asks each of those once for all of the pod's
groups, not once per group.

A person on the page is keyed by who they are to the pod -- ``member:<id>`` or
``contact:<id>`` -- never by their platform id: on WhatsApp that is a phone
number, and the page is every member's to read.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts import channel_conversations
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.domain.groups import SurfaceGroup
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (  # noqa: E501
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_page_repository import (  # noqa: E501
    GroupPageRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.notification_repository import (  # noqa: E501
    NotificationRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.services.group_log import keeps_group_log
from app.modules.channels.contracts import (
    ChannelMessage,
    Principal,
    Reader,
    SenderKind,
    Speaker,
    background,
    speakers_in,
)


class GroupPerson(BaseModel):
    name: str
    external_id: str | None = None
    user_id: UUID | None = None
    in_pod: bool = False


class WaitingQuestion(BaseModel):
    notification_id: UUID
    question: str
    asked_at: datetime


class GroupSummary(BaseModel):
    group: SurfaceGroup
    surface_name: str
    owner_name: str | None = None
    #: Whether the owner is still in the pod. One who has left answers for
    #: nobody, and the group is anybody's to take on.
    owner_in_pod: bool = False
    #: The bot's own switch for people outside the pod, over all its groups.
    bot_answers_outsiders: bool = True
    #: People seen speaking in the group, in and outside the pod; None where
    #: the pod keeps no log of the group (Slack reads its own history).
    members: int | None = None
    outsiders: int | None = None
    last_message_at: datetime | None = None
    waiting_for_viewer: int = 0

    @property
    def answers_outsiders_now(self) -> bool:
        """Whether a stranger who asks in this group today is answered."""
        return (
            self.group.welcomes_outsiders
            and self.owner_in_pod
            and self.bot_answers_outsiders
        )


class GroupDetail(GroupSummary):
    people: list[GroupPerson] = []
    waiting: list[WaitingQuestion] = []


class TimelineLine(BaseModel):
    author_name: str | None = None
    #: Who said it, as the page keys people (``person_key``).
    author_key: str | None = None
    in_pod: bool
    from_agent: bool = False
    text: str
    created_at: datetime
    #: On the bot's lines: whom it answered, and whether from what is Public.
    answered_name: str | None = None
    answered_from_public: bool = False
    #: An answer the bot made with another member's own access: the page says
    #: it was given, and to whom, but not what it said.
    withheld: bool = False


def person_key(principal: Principal) -> str:
    """How the page names a person: by who they are to the pod."""
    if principal.contact_id is not None:
        return f"contact:{principal.contact_id}"
    return f"member:{principal.user_id}"


class SpaceGroups:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.groups = SurfaceGroupRepository(uow.session)
        self.page = GroupPageRepository(uow.session)
        self.people = SqlAlchemySurfaceRoutingResolutionAdapter(uow)

    async def summaries(self, *, pod_id: UUID, viewer_id: UUID) -> list[GroupSummary]:
        surfaces = await self._surfaces(pod_id)
        groups = [
            group
            for group in await self.groups.list_for_pod(pod_id)
            if group.surface_id in surfaces
        ]
        return await self._summarise(
            groups, surfaces=surfaces, pod_id=pod_id, viewer_id=viewer_id
        )

    async def detail(
        self, *, pod_id: UUID, group_id: UUID, viewer_id: UUID
    ) -> GroupDetail | None:
        group = await self._in_pod(pod_id=pod_id, group_id=group_id)
        if group is None:
            return None
        surfaces = await self._surfaces(pod_id)
        if group.surface_id not in surfaces:
            return None
        [summary] = await self._summarise(
            [group], surfaces=surfaces, pod_id=pod_id, viewer_id=viewer_id
        )
        speakers = (await self._speakers([group])).get(group.id, [])
        in_pod = await self.people.pod_members_among(pod_id, _proven(speakers))
        return GroupDetail(
            **summary.model_dump(),
            people=_people(speakers, in_pod=in_pod),
            waiting=await self._waiting(group, viewer_id=viewer_id),
        )

    async def timeline(
        self, *, pod_id: UUID, group_id: UUID, viewer_id: UUID, limit: int
    ) -> list[TimelineLine] | None:
        group = await self._in_pod(pod_id=pod_id, group_id=group_id)
        if group is None:
            return None
        lines = await background(
            self.uow, channel_id=group.channel_id, reader=Reader.member(), limit=limit
        )
        names = {
            speaker.principal: speaker.name
            for speaker in await speakers_in(self.uow, [group.channel_id])
        }
        members = await self.people.pod_members_among(
            pod_id, {line.sender.user_id for line in lines if line.sender.user_id}
        )
        return [
            timeline_line(line, members=members, names=names, viewer_id=viewer_id)
            for line in lines
            if not line.members_only
        ]

    # ---------------------------------------------------------------- helpers

    async def _summarise(
        self,
        groups: Sequence[SurfaceGroup],
        *,
        surfaces: dict[UUID, AgentSurfaceEntity],
        pod_id: UUID,
        viewer_id: UUID,
    ) -> list[GroupSummary]:
        """Every group's summary from one read of each kind, whatever their number."""
        if not groups:
            return []
        speakers = await self._speakers(groups)
        owners = {group.owner_user_id for group in groups if group.owner_user_id}
        everyone = [speaker for said in speakers.values() for speaker in said]
        in_pod = await self.people.pod_members_among(pod_id, owners | _proven(everyone))
        names = await self.people.display_names(owners)
        waiting = await self._waiting_counts(groups, viewer_id=viewer_id)
        return [
            _summary(
                group,
                surface=surfaces[group.surface_id],
                speakers=speakers.get(group.id, []),
                in_pod=in_pod,
                owner_name=names.get(group.owner_user_id)
                if group.owner_user_id
                else None,
                last_message_at=group.last_inbound_at,
                waiting=waiting.get(group.id, 0),
            )
            for group in groups
        ]

    async def _speakers(
        self, groups: Sequence[SurfaceGroup]
    ) -> dict[UUID, list[Speaker]]:
        """Who has spoken in each group the pod keeps a log of, newest first."""
        group_of = {
            group.channel_id: group.id
            for group in groups
            if keeps_group_log(group.platform)
        }
        by_group: dict[UUID, list[Speaker]] = defaultdict(list)
        for speaker in await speakers_in(self.uow, group_of.keys()):
            by_group[group_of[speaker.channel_id]].append(speaker)
        return by_group

    async def _waiting_counts(
        self, groups: Sequence[SurfaceGroup], *, viewer_id: UUID
    ) -> dict[UUID, int]:
        group_of = {group.channel_id: group.id for group in groups}
        conversations = {
            group_of[channel_id]: ids
            for channel_id, ids in (
                await channel_conversations.contact_conversations_in(
                    self.uow, group_of.keys()
                )
            ).items()
        }
        counts = await self.page.open_asks_by_conversation(
            recipient_user_id=viewer_id,
            conversation_ids=[c for ids in conversations.values() for c in ids],
        )
        return {
            group_id: sum(counts.get(conversation, 0) for conversation in ids)
            for group_id, ids in conversations.items()
        }

    async def group_in_pod(
        self, *, pod_id: UUID, group_id: UUID
    ) -> SurfaceGroup | None:
        """The group, when it is this pod's."""
        return await self._in_pod(pod_id=pod_id, group_id=group_id)

    async def surface(self, surface_id: UUID) -> AgentSurfaceEntity | None:
        """The bot a group belongs to."""
        return await SurfaceRepository(self.uow).get(surface_id)

    async def change(
        self,
        group_id: UUID,
        *,
        new_owner: UUID | None,
        answers_outsiders: bool | None,
    ) -> None:
        """Take a group over, switch its outsiders, or both; each only if given."""
        if new_owner is not None:
            await self.groups.set_owner(group_id, new_owner)
        if answers_outsiders is not None:
            await self.groups.set_answers_outsiders(
                group_id, answers_outsiders=answers_outsiders
            )

    async def _in_pod(self, *, pod_id: UUID, group_id: UUID) -> SurfaceGroup | None:
        group = await self.groups.get_by_id(group_id)
        return group if group is not None and group.pod_id == pod_id else None

    async def _surfaces(self, pod_id: UUID) -> dict[UUID, AgentSurfaceEntity]:
        surfaces, _ = await SurfaceRepository(self.uow).list_by_pod(pod_id)
        return {surface.id: surface for surface in surfaces}

    async def _waiting(
        self, group: SurfaceGroup, *, viewer_id: UUID
    ) -> list[WaitingQuestion]:
        """What this group's people outside the pod are waiting on the viewer for."""
        conversations = (
            await channel_conversations.contact_conversations_in(
                self.uow, [group.channel_id]
            )
        ).get(group.channel_id, [])
        asks = await NotificationRepository(self.uow).list_open_asks_from(
            recipient_user_id=viewer_id, origin_conversation_ids=conversations
        )
        return [
            WaitingQuestion(
                notification_id=ask.id,
                question=ask.body,
                asked_at=ask.created_at,
            )
            for ask in asks
        ]


def _summary(
    group: SurfaceGroup,
    *,
    surface: AgentSurfaceEntity,
    speakers: Sequence[Speaker],
    in_pod: set[UUID],
    owner_name: str | None,
    last_message_at: datetime | None,
    waiting: int,
) -> GroupSummary:
    members: int | None = None
    outsiders: int | None = None
    if keeps_group_log(group.platform):
        members = sum(1 for speaker in speakers if speaker.principal.user_id in in_pod)
        outsiders = len(speakers) - members
    return GroupSummary(
        group=group,
        surface_name=surface.name,
        owner_name=owner_name,
        owner_in_pod=group.owner_user_id in in_pod,
        bot_answers_outsiders=surface.config.groups.answers_outsiders,
        members=members,
        outsiders=outsiders,
        last_message_at=last_message_at,
        waiting_for_viewer=waiting,
    )


def _proven(speakers: Iterable[Speaker]) -> set[UUID]:
    return {
        speaker.principal.user_id for speaker in speakers if speaker.principal.user_id
    }


def _people(speakers: Sequence[Speaker], *, in_pod: set[UUID]) -> list[GroupPerson]:
    """Everyone who has spoken in the group, once each, most recent first."""
    return [
        GroupPerson(
            name=_shown(speaker.name),
            external_id=person_key(speaker.principal),
            user_id=speaker.principal.user_id,
            in_pod=speaker.principal.user_id in in_pod,
        )
        for speaker in sorted(speakers, key=lambda s: s.last_said_at, reverse=True)
    ]


def timeline_line(
    line: ChannelMessage,
    *,
    members: set[UUID],
    names: dict[Principal, str | None],
    viewer_id: UUID,
) -> TimelineLine:
    """One line of the group's page, as ``viewer_id`` reads it.

    An answer the bot made with a member's own access is withheld from every
    other member: the page says it was given, and to whom, but not what it
    said, because the reader may not be able to see what it was made from.
    """
    sender = line.sender
    from_agent = sender.kind is SenderKind.AGENT
    behalf = sender.on_behalf_of
    principal = (
        None
        if from_agent or (sender.user_id is None and sender.contact_id is None)
        else Principal(user_id=sender.user_id, contact_id=sender.contact_id)
    )
    return TimelineLine(
        author_name=sender.display_name,
        author_key=person_key(principal) if principal else None,
        in_pod=from_agent or sender.user_id in members,
        from_agent=from_agent,
        text=line.text,
        created_at=line.created_at,
        answered_name=names.get(behalf) if behalf else None,
        answered_from_public=from_agent and not sender.used_a_members_access,
        withheld=from_agent
        and sender.used_a_members_access
        and (behalf is None or behalf.user_id != viewer_id),
    )


def _shown(name: str | None) -> str:
    """A speaker's name for the page; one the platform never gave stays unnamed."""
    return " ".join(str(name or "").split()) or "Unnamed"
