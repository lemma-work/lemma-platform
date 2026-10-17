"""What a pod's bot has heard in a group, kept where the platform keeps nothing.

Every turn in a group runs in one person's conversation, so a run knows the
group only through the background it is handed. Slack and Teams can fetch that
fresh -- their history APIs are the source of truth. Telegram cannot (a bot may
not read a group's history, and hears only what privacy mode lets through), and
neither can WhatsApp. For those every group message the bot receives is a line
of the group's channel (``app.modules.channels``), said by the member or the
contact who wrote it, so the next turn -- a member's or a contact's -- knows
what was said. The bot's own answers join it as they are delivered
(``channel_lines``).

Logged only for groups the pod already knows (see ``domain/groups``). A chat
nobody has brought the bot into properly is not recorded: there is nobody to
answer for it, and nothing that reads the log would run there.

Who wrote a line is decided as it is written. Somebody the pod has resolved to
one of its members is that member; anybody else is a contact, made the first
time they speak -- whether or not the bot answers strangers here, because a
line said by nobody is a timeline nobody can read.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence, Set
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.groups import SurfaceGroup
from app.modules.agent_surfaces.domain.models import (
    SurfaceContextMessage,
    SurfaceGroupParticipant,
)
from app.modules.agent_surfaces.domain.ports import SurfacePodMembershipPort
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (  # noqa: E501
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (  # noqa: E501
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    PLATFORM_CAPABILITIES,
)
from app.modules.agent_surfaces.services.sender_handles import group_handle
from app.modules.agent_surfaces.services.slack_groups import note_slack_channel
from app.modules.agent_surfaces.services.channel_places import (
    ChannelSeats,
    ChannelSeatsInDatabase,
)
from app.modules.channels.contracts import (
    Principal,
    Reader,
    Sender,
    SenderKind,
    Source,
    background,
    may_read,
    post,
    source_by_id,
    speakers_in,
)
from app.modules.contacts.contracts import IdentityStrength, open_contact

logger = get_logger(__name__)


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


async def group_source(uow: SqlAlchemyUnitOfWork, group: SurfaceGroup) -> Source | None:
    """The group's place, as channels keep it: the group is its source."""
    return await source_by_id(uow, group.id)


class GroupLog:
    def __init__(
        self, uow: SqlAlchemyUnitOfWork, *, seats: ChannelSeats | None = None
    ) -> None:
        self.uow = uow
        self.seats = seats or ChannelSeatsInDatabase(uow)
        self.groups = SurfaceGroupRepository(uow.session)
        self.membership: SurfacePodMembershipPort = (
            SqlAlchemySurfaceRoutingResolutionAdapter(uow)
        )
        self.external_users = ExternalSurfaceUserRepository(uow)

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
            return await note_slack_channel(self.groups, self.seats, surfaces, parsed)
        if (
            not keeps_group_log(parsed.platform.value)
            or not parsed.message_text.strip()
        ):
            return False
        known = await self.groups.list_for_channel(
            external_channel_id=parsed.external_channel_id,
            surface_ids=[surface.id for surface in surfaces],
        )
        for group in known:
            await post(
                self.uow,
                channel_id=group.channel_id,
                sender=await self._speaker(group, parsed),
                text=parsed.message_text,
                source_id=group.id,
                external_message_id=parsed.external_message_id,
            )
        return bool(known)

    async def _speaker(
        self, group: SurfaceGroup, parsed: ParsedInboundSurfaceEvent
    ) -> Sender:
        """Who said it: the member the pod resolved them to, else a contact."""
        name = parsed.sender_display_name
        external_id = parsed.sender_external_user_id
        if external_id:
            resolved = await self.external_users.resolved_users_by_external_ids(
                platform=group.platform,
                tenant_id=parsed.tenant_id,
                external_user_ids=[external_id],
            )
            user_id = resolved.get(external_id)
            if (
                user_id is not None
                and user_id
                in await self.membership.pod_members_among(group.pod_id, [user_id])
            ):
                return Sender.member(user_id, display_name=name)
        handle = group_handle(parsed)
        if handle is None or not handle.vouched_for:
            # Nobody the platform vouches for: still a line the group saw, said
            # by somebody the pod cannot name as a contact.
            return Sender(kind=SenderKind.CONTACT, display_name=name)
        contact = await open_contact(
            self.uow,
            pod_id=group.pod_id,
            kind=handle.kind,
            value=handle.value,
            strength=IdentityStrength.CHANNEL,
            display_name=name,
        )
        return Sender.contact(contact.id, display_name=name)


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
    #: Empty for a contact's run, which is told no more about the pod than it
    #: can already read.
    participants: tuple[SurfaceGroupParticipant, ...] = ()


async def group_background(
    uow: SqlAlchemyUnitOfWork,
    *,
    group: SurfaceGroup,
    source: Source,
    membership: SurfacePodMembershipPort,
    agent_display_name: str | None,
    reader: Reader | None,
    asker: Principal | None = None,
    limit: int = GROUP_CONTEXT_LINES,
) -> GroupBackground:
    """The group's recent lines, as the background a run is handed.

    Different for the two kinds of run, because they act with different access:

    * **A contact's run** reads what the channel lets a contact read
      (``channels.domain.projection``): this group's lines, but no answer the
      bot made with a member's own access -- most from before this person was
      in the group.
    * **A member's run** acts with all of the member's access, so it is not
      shown what people outside the pod wrote, nor the bot's answers to them:
      nothing a stranger steered reaches it as background. Who they are is
      kept, so the run can be told that they read what it posts. Nor a note
      the pod's members left one another: the answer goes to the whole group.

    ``reader`` is ``None`` for a run that is neither -- another pod asking --
    which is shown nothing.
    """
    if reader is None:
        return GroupBackground(lines=[])
    # Read as the pod reads it, then held to what this reader may see, so what
    # is left out is counted rather than silently missing.
    lines = await background(
        uow, channel_id=source.channel_id, reader=Reader.member(), limit=limit
    )
    members = await membership.pod_members_among(
        group.pod_id, {line.sender.user_id for line in lines if line.sender.user_id}
    )
    kept: list[SurfaceContextMessage] = []
    outside_authors: list[str] = []
    withheld = 0
    for line in lines:
        from_agent = line.sender.kind is SenderKind.AGENT
        outside = not from_agent and line.sender.user_id not in members
        author = (
            agent_display_name or "the bot" if from_agent else line.sender.display_name
        )
        if outside and author:
            outside_authors.append(author)
        if reader.is_member:
            # Not a members' note either: the answer is posted where everybody
            # in the group reads it.
            shown = not (
                line.members_only
                or outside
                or (from_agent and not line.sender.used_a_members_access)
            )
        else:
            shown = may_read(reader, line)
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
        await _participants(
            uow, group=group, source=source, membership=membership, asker=asker
        )
        if reader.is_member
        else ()
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


#: The two reads a live group's roster asks, over one unit of work: who the pod
#: has resolved each platform sender to, and which of those are in the pod.
RosterReads = Callable[
    [SqlAlchemyUnitOfWork],
    tuple[SurfacePodMembershipPort, ExternalSurfaceUserRepository],
]


def roster_reads(
    uow: SqlAlchemyUnitOfWork,
) -> tuple[SurfacePodMembershipPort, ExternalSurfaceUserRepository]:
    membership = SqlAlchemySurfaceRoutingResolutionAdapter(uow)
    return membership, ExternalSurfaceUserRepository(uow)


async def live_participants(
    uow_factory: UnitOfWorkFactory,
    *,
    lines: Sequence[Mapping[str, object]],
    pod_id: UUID,
    platform: str,
    tenant_id: str | None,
    asker_external_id: str | None,
    conversation_id: UUID,
    reads: RosterReads = roster_reads,
) -> tuple[SurfaceGroupParticipant, ...]:
    """The roster of a group whose history was fetched live (Slack, Teams).

    The platform hands over lines and nothing else, and a line says who spoke
    rather than who holds access, so the roster is the pod's own answer about
    those speakers (``pod_members_in_lines``). A colleague in the same
    workspace who never spoke to the bot, and anyone there with no Lemma
    account, is named as outside the pod rather than as a member.

    Nothing where nothing was fetched: an empty roster claims nothing, where a
    guessed one claims access somebody may not hold. The same answer when the
    read fails -- the history beside it is best-effort, and a roster is not a
    reason to fail the member's turn and replay it from the top.
    """
    if not lines:
        return ()
    try:
        async with uow_factory() as uow:
            membership, external_users = reads(uow)
            verified = await pod_members_in_lines(
                lines=lines,
                pod_id=pod_id,
                platform=platform,
                tenant_id=tenant_id,
                membership=membership,
                external_users=external_users,
            )
    except (SQLAlchemyError, OSError) as exc:
        # The database's family, because the database is all this touches.
        logger.warning(
            "agent_surfaces.ingress_service.group_roster_failed.degraded",
            conversation_id=str(conversation_id),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return ()
    return participants_in_lines(
        lines, verified=verified, asker_external_id=asker_external_id
    )


def participants_in_lines(
    lines: Sequence[Mapping[str, object]],
    *,
    verified: Set[str] = frozenset(),
    asker_external_id: str | None = None,
) -> tuple[SurfaceGroupParticipant, ...]:
    """The people a fetched history names, and which of them hold access here.

    Where the pod keeps no log of a group -- Slack and Teams read their own
    history live -- the platform's own lines are the only record of who is
    there. Membership is not read off them: ``verified`` is the platform ids the
    pod itself says belong to it (``pod_members_in_lines``), and a speaker whose
    id is not in it is somebody the pod cannot vouch for. Saying otherwise is
    how a room of clients came to read like a room of colleagues.

    One entry per person, told apart by their platform id where the line has
    one: two people may show the same name, and folding them by it let an
    outsider who took a member's name drop out of the roster behind them. The
    asker is not on it -- the roster is who *else* is there -- and nor is an
    app's line, the bot's own answers among them: an app is nobody to answer
    in front of, and named as outside the pod it read as a stranger. An author
    still showing their bare platform id has no name, and is counted as such.
    """
    seen: list[SurfaceGroupParticipant] = []
    people: set[str] = set()
    for line in lines:
        external_id = _external_id(line)
        shown = " ".join(str(line.get("author") or "").split())
        name = "" if shown == external_id else shown
        if (
            not (name or external_id)
            or line.get("from_bot")
            or (asker_external_id and external_id == asker_external_id)
        ):
            continue
        person = f"id:{external_id}" if external_id else f"name:{name.casefold()}"
        if person in people:
            continue
        people.add(person)
        seen.append(SurfaceGroupParticipant(name=name, in_pod=external_id in verified))
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


async def _participants(
    uow: SqlAlchemyUnitOfWork,
    *,
    group: SurfaceGroup,
    source: Source,
    membership: SurfacePodMembershipPort,
    asker: Principal | None,
) -> tuple[SurfaceGroupParticipant, ...]:
    """Who the pod knows is in this group, and which of them hold access to it.

    Read from everyone who has spoken in the group's channel rather than from
    the lines this run is shown: a member who spoke last week is still in the
    group today, and the run that answers in front of them should know so. On
    the platforms whose history cannot be fetched that is the pod's whole
    knowledge of a group.

    One entry per person -- never folded by name, since an outsider showing a
    member's name is two people, and only one of them holds access. The asker
    is left off: it is who *else* is there. A speaker the pod has no name for
    stays on it unnamed, never under their platform id -- on WhatsApp that is
    their phone number, and the answer is posted where everybody reads it.
    """
    speakers = await speakers_in(uow, [source.channel_id], limit=_MAX_PARTICIPANTS + 1)
    recent = [speaker for speaker in speakers if speaker.principal != asker][
        :_MAX_PARTICIPANTS
    ]
    members = await membership.pod_members_among(
        group.pod_id,
        {speaker.principal.user_id for speaker in recent if speaker.principal.user_id},
    )
    return tuple(
        SurfaceGroupParticipant(
            name=" ".join(str(speaker.name or "").split()),
            in_pod=speaker.principal.user_id in members,
        )
        for speaker in recent
    )
