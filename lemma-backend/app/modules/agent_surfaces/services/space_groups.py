"""A pod's groups, as the people in the pod see them.

The chats on WhatsApp, Telegram and Slack that the pod's bot is in, gathered in
one place: what each is called, who in it is in the pod and who is not, who
answers for the ones who are not, and what is waiting on the person looking.

Everyone in the pod may read a group's page -- it is the pod's group, and its
bot speaks for the pod there. What never appears is anybody's private asking:
a member's own conversation stays theirs, and a note written in Lemma is never
a line of the group.

Everything here is a bounded read over rows other modules already keep: the
group registry and its log (``group_repository``), the thread links that name
a group's outsiders conversation, and the notifications that conversation sent
the member who answers for it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.domain.groups import (
    OUTSIDERS_LINK_USER,
    PASSED_ON_FROM_OUTSIDE,
    GroupLine,
    SurfaceGroup,
)
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (  # noqa: E501
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (  # noqa: E501
    SurfaceConversationLinkRepository,
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

#: How much of a group's log decides who is in it. People who have not spoken
#: in that long are not counted; the page says "people who have spoken here".
_PEOPLE_WINDOW = 200


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
    #: People seen speaking in the group, in and outside the pod; None where
    #: the pod keeps no log of the group (Slack reads its own history).
    members: int | None = None
    outsiders: int | None = None
    last_message_at: datetime | None = None
    waiting_for_viewer: int = 0


class GroupDetail(GroupSummary):
    people: list[GroupPerson] = []
    waiting: list[WaitingQuestion] = []


class TimelineLine(BaseModel):
    line: GroupLine
    in_pod: bool


class SpaceGroups:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.groups = SurfaceGroupRepository(uow.session)
        self.people = SqlAlchemySurfaceRoutingResolutionAdapter(uow)

    async def summaries(self, *, pod_id: UUID, viewer_id: UUID) -> list[GroupSummary]:
        groups = await self.groups.list_for_pod(pod_id)
        surfaces = await self._surfaces(pod_id)
        last = await self.groups.latest_line_at([group.id for group in groups])
        summaries = []
        for group in groups:
            surface = surfaces.get(group.surface_id)
            if surface is None:
                continue
            summaries.append(
                await self._summary(
                    group,
                    surface=surface,
                    viewer_id=viewer_id,
                    last_message_at=last.get(group.id),
                )
            )
        return summaries

    async def detail(
        self, *, pod_id: UUID, group_id: UUID, viewer_id: UUID
    ) -> GroupDetail | None:
        group = await self._in_pod(pod_id=pod_id, group_id=group_id)
        if group is None:
            return None
        surface = (await self._surfaces(pod_id)).get(group.surface_id)
        if surface is None:
            return None
        last = await self.groups.latest_line_at([group.id])
        summary = await self._summary(
            group,
            surface=surface,
            viewer_id=viewer_id,
            last_message_at=last.get(group.id),
        )
        lines = await self._recent(group)
        members = await self._members_among(lines, pod_id=pod_id)
        return GroupDetail(
            **summary.model_dump(),
            people=_people(lines, members=members),
            waiting=await self._waiting(group, viewer_id=viewer_id),
        )

    async def timeline(
        self, *, pod_id: UUID, group_id: UUID, limit: int
    ) -> list[TimelineLine] | None:
        group = await self._in_pod(pod_id=pod_id, group_id=group_id)
        if group is None:
            return None
        lines = await self.groups.recent_lines(group=group, limit=limit)
        members = await self._members_among(lines, pod_id=pod_id)
        return [
            TimelineLine(
                line=line,
                in_pod=line.from_agent or line.author_user_id in members,
            )
            for line in lines
        ]

    # ---------------------------------------------------------------- helpers

    async def _in_pod(self, *, pod_id: UUID, group_id: UUID) -> SurfaceGroup | None:
        group = await self.groups.get_by_id(group_id)
        return group if group is not None and group.pod_id == pod_id else None

    async def _surfaces(self, pod_id: UUID) -> dict[UUID, AgentSurfaceEntity]:
        surfaces, _ = await SurfaceRepository(self.uow).list_by_pod(pod_id)
        return {surface.id: surface for surface in surfaces}

    async def _summary(
        self,
        group: SurfaceGroup,
        *,
        surface: AgentSurfaceEntity,
        viewer_id: UUID,
        last_message_at: datetime | None,
    ) -> GroupSummary:
        owner_name = (
            await self.people.get_user_display_name(group.owner_user_id)
            if group.owner_user_id is not None
            else None
        )
        members: int | None = None
        outsiders: int | None = None
        if keeps_group_log(group.platform):
            lines = await self._recent(group)
            in_pod = await self._members_among(lines, pod_id=group.pod_id)
            people = _people(lines, members=in_pod)
            members = sum(1 for person in people if person.in_pod)
            outsiders = len(people) - members
        return GroupSummary(
            group=group,
            surface_name=surface.name,
            owner_name=owner_name,
            members=members,
            outsiders=outsiders,
            last_message_at=last_message_at,
            waiting_for_viewer=len(await self._waiting(group, viewer_id=viewer_id)),
        )

    async def _recent(self, group: SurfaceGroup) -> list[GroupLine]:
        if not keeps_group_log(group.platform):
            return []
        return await self.groups.recent_lines(group=group, limit=_PEOPLE_WINDOW)

    async def _members_among(
        self, lines: Sequence[GroupLine], *, pod_id: UUID
    ) -> set[UUID]:
        members: set[UUID] = set()
        for user_id in {line.author_user_id for line in lines if line.author_user_id}:
            if pod_id in await self.people.get_user_pod_ids(user_id):
                members.add(user_id)
        return members

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
                question=_as_asked(ask.body),
                asked_at=ask.created_at,
            )
            for ask in asks
        ]


def _as_asked(body: str) -> str:
    """The question as the bot put it, without the line saying where it came from."""
    return body.removesuffix(PASSED_ON_FROM_OUTSIDE).rstrip()


def _people(lines: Sequence[GroupLine], *, members: set[UUID]) -> list[GroupPerson]:
    """Everyone who has spoken in the group, once each, most recent first."""
    seen: dict[str, GroupPerson] = {}
    for line in reversed(lines):
        if line.from_agent or not line.author_external_id:
            continue
        if line.author_external_id in seen:
            continue
        seen[line.author_external_id] = GroupPerson(
            name=line.author_name or line.author_external_id,
            external_id=line.author_external_id,
            user_id=line.author_user_id,
            in_pod=line.author_user_id in members,
        )
    return list(seen.values())
