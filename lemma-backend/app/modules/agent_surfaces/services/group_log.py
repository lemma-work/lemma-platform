"""What a pod's bot has heard in a group, kept where the platform keeps nothing.

Every turn in a group runs in one person's conversation, so a run knows the
group only through the background it is handed. Slack and Teams can fetch that
fresh -- their history APIs are the source of truth, and nothing is logged for
them. Telegram cannot (a bot may not read a group's history, and hears only
what privacy mode lets through), and neither can WhatsApp. For those the pod
keeps its own log: every group message the bot receives, and every answer it
gives, so the next turn -- a member's or a stranger's -- knows what was said.

Logged only for groups the pod already knows (see ``domain/groups``). A chat
nobody has brought the bot into properly is not recorded: there is nobody to
answer for it, and nothing that reads the log would run there.

Who wrote each line is read at the moment the lines are read, against the pod's
membership now. A stranger who has joined since reads as a member; a member who
has left reads as a stranger.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.groups import (
    OUTSIDERS_LINK_USER,
    GroupLine,
    SurfaceGroup,
    answer_withheld_from,
)
from app.modules.agent_surfaces.domain.models import (
    SurfaceContextMessage,
    SurfaceGroupParticipant,
)
from app.modules.agent_surfaces.domain.ports import SurfacePodMembershipPort
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (  # noqa: E501
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_page_repository import (
    GroupPageRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    PLATFORM_CAPABILITIES,
)
from app.modules.agent_surfaces.services.slack_groups import note_slack_channel


@dataclass(frozen=True, slots=True)
class Answered:
    """Whom a line the bot wrote in a group was for, and on whose access."""

    name: str | None
    #: For somebody outside the pod, from what the pod made Public -- rather
    #: than for a member, with that member's own access.
    from_public: bool
    #: The member whose access it was made with; None when from Public.
    user_id: UUID | None = None


def answered_in_group(
    link: AgentSurfaceConversationLink, conversation_user_id: UUID | None = None
) -> Answered:
    """Who a delivery into a group answered, read off the thread it answered in.

    The link is keyed by the person asking, and a conversation answering people
    outside the pod shares one key for all of them -- so its last inbound event
    is what names the particular stranger this answer was for. Any other
    conversation in a group is one member's, run with their access: the group's
    page shows what it said to that member alone.
    """
    last_event = link.last_event or {}
    name = last_event.get("sender_display_name")
    from_public = link.external_user_id == OUTSIDERS_LINK_USER
    return Answered(
        name=name if isinstance(name, str) and name.strip() else None,
        from_public=from_public,
        user_id=None if from_public else conversation_user_id,
    )


#: How much of a group a run is shown. The same window a Slack run fetches, so a
#: group reads the same to the agent whichever platform it is on.
GROUP_CONTEXT_LINES = 15

#: How many of a group's people go in front of the model. The roster is who the
#: pod knows is there, and a group past this many speakers has outgrown a list.
_MAX_PARTICIPANTS = 20


def keeps_group_log(platform: str) -> bool:
    """Whether this platform's groups are logged by the pod rather than read live."""
    capabilities = PLATFORM_CAPABILITIES.get(str(platform).upper())
    return capabilities is not None and not capabilities.reads_channel_history


class GroupLog:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.groups = SurfaceGroupRepository(uow.session)

    async def note_inbound(
        self,
        surfaces: Sequence[AgentSurfaceEntity],
        parsed: ParsedInboundSurfaceEvent,
    ) -> bool:
        """Log a group message for every one of these surfaces that knows the group.

        Called before anything decides whether the message is for the bot,
        because most of what is said in a group is not -- and all of it is what
        "what did we decide yesterday?" is asking about. Says whether anything
        was written, so the caller commits only when there is something to.
        """
        if parsed.is_dm or not parsed.external_channel_id:
            return False
        if parsed.platform is SurfacePlatform.SLACK:
            # Slack keeps its own history; the pod keeps only the channel's row.
            return await note_slack_channel(self.groups, surfaces, parsed)
        if not keeps_group_log(parsed.platform.value):
            return False
        known = await self.groups.list_for_channel(
            external_channel_id=parsed.external_channel_id,
            surface_ids=[surface.id for surface in surfaces],
        )
        for group in known:
            await self.groups.append_line(
                group_id=group.id,
                body=parsed.message_text,
                external_message_id=parsed.external_message_id,
                author_external_id=parsed.sender_external_user_id,
                author_name=parsed.sender_display_name,
            )
        return bool(known)

    async def note_agent_answer(
        self,
        *,
        surface: AgentSurfaceEntity,
        external_channel_id: str | None,
        text: str,
        answered: Answered | None = None,
    ) -> None:
        """Log what the bot said in a group, once it has been delivered."""
        if not external_channel_id or not keeps_group_log(surface.surface_type.value):
            return
        group = await self.groups.get(
            surface_id=surface.id, external_channel_id=external_channel_id
        )
        if group is None:
            return
        await self.groups.append_line(
            group_id=group.id,
            body=text,
            from_agent=True,
            answered_name=answered.name if answered else None,
            answered_from_public=answered.from_public if answered else False,
            answered_user_id=answered.user_id if answered else None,
        )


@dataclass(frozen=True, slots=True)
class GroupBackground:
    """What a run is shown of a group, and how much it was not shown."""

    lines: list[SurfaceContextMessage]
    #: Lines left out because this run may not read them.
    withheld: int = 0
    #: Who, among the lines' authors, is outside the pod -- for a member run's
    #: notice of who else will read its answer.
    outside_authors: tuple[str, ...] = ()
    #: Everyone the pod knows is in the group, and whether they are in the pod.
    #: Empty for a stranger's run, which acts as nobody and is told no more
    #: about the pod than it can already read.
    participants: tuple[SurfaceGroupParticipant, ...] = ()


async def group_background(
    uow: SqlAlchemyUnitOfWork,
    *,
    group: SurfaceGroup,
    membership: SurfacePodMembershipPort,
    agent_display_name: str | None,
    for_stranger: bool,
    limit: int = GROUP_CONTEXT_LINES,
) -> GroupBackground:
    """The group's recent lines, as the background a run is handed.

    Different for the two kinds of run, because they act with different access:

    * **A stranger's run** reads as nobody, so it is not shown an answer the bot
      made with a member's own access (``answer_withheld_from``) -- the log keeps
      ninety days of them, most from before this person was in the group.
    * **A member's run** acts with all of the member's access, so it is not
      shown what strangers wrote, nor the bot's answers to them: nothing a
      stranger steered reaches it as background. Who they are is kept, so the
      run can be told that they read what it posts.
    """
    lines = await SurfaceGroupRepository(uow.session).recent_lines(
        group=group, limit=limit
    )
    members = await _members_among(lines, pod_id=group.pod_id, membership=membership)
    kept: list[SurfaceContextMessage] = []
    outside_authors: list[str] = []
    withheld = 0
    for line in lines:
        outside = not line.from_agent and line.author_user_id not in members
        author = (
            agent_display_name or "the bot"
            if line.from_agent
            else line.author_name or line.author_external_id
        )
        if outside and author:
            outside_authors.append(author)
        if for_stranger:
            shown = not answer_withheld_from(line, None)
        else:
            shown = not outside and not (line.from_agent and line.answered_from_public)
        if not shown:
            withheld += 1
            continue
        kept.append(
            SurfaceContextMessage(
                author=author,
                text=line.text,
                ts=line.created_at.isoformat(),
                outside_pod=outside,
            )
        )
    participants = (
        ()
        if for_stranger
        else await _participants(uow, group=group, membership=membership)
    )
    return GroupBackground(
        lines=kept,
        withheld=withheld,
        outside_authors=tuple(outside_authors),
        participants=participants,
    )


async def pod_members_in_lines(
    *,
    lines: Sequence[Mapping[str, object]],
    pod_id: UUID,
    platform: str,
    tenant_id: str | None,
    membership: SurfacePodMembershipPort,
    external_users: ExternalSurfaceUserRepository,
) -> set[str]:
    """Which of a fetched history's speakers the pod knows to be members of it.

    Where the pod keeps no log of a group -- Slack and Teams read their own
    history live -- a platform's lines are the only record of who is there, and
    a line says who spoke, never who holds access. What the platform marks is
    not that either: Slack marks a line from another company's workspace, and a
    colleague in the pod's own workspace who has no Lemma account reads exactly
    like one who does. So the question is put to the pod instead: the speakers
    it has already resolved to a Lemma user on this platform, and of those the
    ones still in this pod. A speaker with no such row -- the colleague who has
    never spoken to the bot, and anybody here without an account -- is in
    neither answer, and is not a member the run may speak from.

    One read per table for a whole window, because the alternative is a lookup
    per person on a path that already runs several.
    """
    wanted = sorted({external for line in lines if (external := _external_id(line))})
    if not wanted:
        return set()
    resolved = await external_users.resolved_users_by_external_ids(
        platform=platform, tenant_id=tenant_id, external_user_ids=wanted
    )
    members = await membership.pod_members_among(pod_id, resolved.values())
    return {external for external, user_id in resolved.items() if user_id in members}


def participants_in_lines(
    lines: Sequence[Mapping[str, object]],
    *,
    verified: Set[str] = frozenset(),
) -> tuple[SurfaceGroupParticipant, ...]:
    """The people a fetched history names, and which of them hold access here.

    Where the pod keeps no log of a group -- Slack and Teams read their own
    history live -- the platform's own lines are the only record of who is
    there. Membership is not read off them: ``verified`` is the platform ids the
    pod itself says belong to it (``pod_members_in_lines``), and a speaker whose
    id is not in it is somebody the pod cannot vouch for. Saying otherwise is
    how a room of clients came to read like a room of colleagues.
    """
    seen: list[SurfaceGroupParticipant] = []
    named: set[str] = set()
    for line in lines:
        name = " ".join(str(line.get("author") or "").split())
        if not name or name.casefold() in named:
            continue
        named.add(name.casefold())
        seen.append(
            SurfaceGroupParticipant(name=name, in_pod=_external_id(line) in verified)
        )
        if len(seen) >= _MAX_PARTICIPANTS:
            break
    return tuple(seen)


def _external_id(line: Mapping[str, object]) -> str:
    """The platform id a line's author was sent under, where the line has one."""
    return str(line.get("author_external_id") or "").strip()


def for_member_run(
    lines: Sequence[dict[str, object]],
) -> tuple[list[dict[str, object]], int]:
    """A platform's history as a member's run may see it: strangers' lines out.

    The same rule ``group_background`` applies to the pod's own log, for the
    platforms whose history is read live (Slack), where a line from another
    workspace is marked ``outside_pod``.
    """
    kept = [line for line in lines if not line.get("outside_pod")]
    return kept, len(lines) - len(kept)


async def _members_among(
    lines: Sequence[GroupLine],
    *,
    pod_id: UUID,
    membership: SurfacePodMembershipPort,
) -> set[UUID]:
    """Which of these lines' authors are in the pod now, in one read."""
    return await membership.pod_members_among(
        pod_id, (line.author_user_id for line in lines if line.author_user_id)
    )


async def _participants(
    uow: SqlAlchemyUnitOfWork,
    *,
    group: SurfaceGroup,
    membership: SurfacePodMembershipPort,
) -> tuple[SurfaceGroupParticipant, ...]:
    """Who the pod knows is in this group, and which of them hold access to it.

    Read from the group's own log rather than from the lines this run is shown:
    a member who spoke last week is still in the group today, and the run that
    answers in front of them should know so. The log is the pod's whole
    knowledge of a group on the platforms whose history cannot be fetched --
    everybody in it who has ever spoken to the bot or past it.
    """
    speakers = await GroupPageRepository(uow.session).speakers([group])
    recent = sorted(speakers, key=lambda speaker: speaker.last_said_at, reverse=True)[
        :_MAX_PARTICIPANTS
    ]
    members = await membership.pod_members_among(
        group.pod_id, (speaker.user_id for speaker in recent if speaker.user_id)
    )
    seen: list[SurfaceGroupParticipant] = []
    named: set[str] = set()
    for speaker in recent:
        name = " ".join(str(speaker.name or speaker.external_id or "").split())
        if not name or name.casefold() in named:
            continue
        named.add(name.casefold())
        seen.append(
            SurfaceGroupParticipant(name=name, in_pod=speaker.user_id in members)
        )
    return tuple(seen)
