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
group registry and its log (``group_repository``), the thread links that name
a group's outsiders conversation, and the notifications that conversation sent
the member who answers for it. The list asks each of those once for all of the
pod's groups (``group_page_repository``), not once per group.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.domain.groups import (
    OUTSIDERS_LINK_USER,
    GroupLine,
    SurfaceGroup,
    answer_withheld_from,
)
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (  # noqa: E501
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (  # noqa: E501
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_page_repository import (  # noqa: E501
    GroupPageRepository,
    GroupSpeaker,
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
    line: GroupLine
    in_pod: bool
    #: An answer the bot made with another member's own access: the page says
    #: it was given, and to whom, but not what it said.
    withheld: bool = False


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
        speakers = await self._speakers([group])
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
        lines = await self.groups.recent_lines(group=group, limit=limit)
        members = await self.people.pod_members_among(
            pod_id, (line.author_user_id for line in lines if line.author_user_id)
        )
        return [
            TimelineLine(
                line=line,
                in_pod=line.from_agent or line.author_user_id in members,
                withheld=answer_withheld_from(line, viewer_id),
            )
            for line in lines
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
        in_pod = await self.people.pod_members_among(pod_id, owners | _proven(speakers))
        names = await self.people.display_names(owners)
        last = await self.groups.latest_line_at([group.id for group in groups])
        waiting = await self._waiting_counts(groups, viewer_id=viewer_id)
        by_group: dict[UUID, list[GroupSpeaker]] = defaultdict(list)
        for speaker in speakers:
            by_group[speaker.group_id].append(speaker)
        return [
            _summary(
                group,
                surface=surfaces[group.surface_id],
                speakers=by_group.get(group.id, []),
                in_pod=in_pod,
                owner_name=names.get(group.owner_user_id)
                if group.owner_user_id
                else None,
                last_message_at=last.get(group.id),
                waiting=waiting.get(group.id, 0),
            )
            for group in groups
        ]

    async def _speakers(self, groups: Sequence[SurfaceGroup]) -> list[GroupSpeaker]:
        return await self.page.speakers(
            [group for group in groups if keeps_group_log(group.platform)]
        )

    async def _waiting_counts(
        self, groups: Sequence[SurfaceGroup], *, viewer_id: UUID
    ) -> dict[UUID, int]:
        conversations = await self.page.outsider_conversations(groups)
        counts = await self.page.open_asks_by_conversation(
            recipient_user_id=viewer_id,
            conversation_ids=[c for ids in conversations.values() for c in ids],
        )
        return {
            group_id: sum(counts.get(conversation, 0) for conversation in ids)
            for group_id, ids in conversations.items()
        }

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
        if group.external_channel_id is None:
            return []
        conversations = await SurfaceConversationLinkRepository(
            self.uow
        ).conversation_ids_in_channel(
            surface_id=group.surface_id,
            external_channel_id=group.external_channel_id,
            external_user_id=OUTSIDERS_LINK_USER,
        )
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
    speakers: Sequence[GroupSpeaker],
    in_pod: set[UUID],
    owner_name: str | None,
    last_message_at: datetime | None,
    waiting: int,
) -> GroupSummary:
    members: int | None = None
    outsiders: int | None = None
    if keeps_group_log(group.platform):
        members = sum(1 for speaker in speakers if speaker.user_id in in_pod)
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


def _proven(speakers: Iterable[GroupSpeaker]) -> set[UUID]:
    return {speaker.user_id for speaker in speakers if speaker.user_id}


def _people(
    speakers: Sequence[GroupSpeaker], *, in_pod: set[UUID]
) -> list[GroupPerson]:
    """Everyone who has spoken in the group, once each, most recent first."""
    return [
        GroupPerson(
            name=speaker.name or speaker.external_id,
            external_id=speaker.external_id,
            user_id=speaker.user_id,
            in_pod=speaker.user_id in in_pod,
        )
        for speaker in sorted(speakers, key=lambda s: s.last_said_at, reverse=True)
    ]
