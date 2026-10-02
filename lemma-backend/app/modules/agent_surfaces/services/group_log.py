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

from collections.abc import Sequence
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
from app.modules.agent_surfaces.domain.models import SurfaceContextMessage
from app.modules.agent_surfaces.domain.ports import SurfacePodMembershipPort
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
    return GroupBackground(
        lines=kept, withheld=withheld, outside_authors=tuple(outside_authors)
    )


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
) -> set[UUID | None]:
    """Which of these lines' authors are in the pod now. One read per author."""
    authors = {line.author_user_id for line in lines if line.author_user_id}
    members: set[UUID | None] = set()
    for user_id in authors:
        if user_id and await membership.get_pod_member_id(user_id, pod_id):
            members.add(user_id)
    return members
