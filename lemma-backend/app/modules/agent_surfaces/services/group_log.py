"""What a pod's bot has heard in a group, as the lines of the group's channel.

Every message the bot receives in a group -- a Telegram or WhatsApp group, a
Slack or Teams channel, a group DM -- is a line of the group's channel
(``app.modules.channels``), said by the member or the contact who wrote it.
That is the timeline the pod's members read in Lemma, and, where the platform
cannot hand a run the group's history, the background the next turn is given:
Telegram (a bot may not read a group's history, and hears only what privacy
mode lets through) and WhatsApp. Slack and Teams channels are read fresh from
the platform, whose history is the whole of it. The bot's own answers join
the timeline as they are delivered (``channel_lines``), and a Slack or Teams
thread is a thread there too.

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
    is_group_dm,
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
from app.modules.agent_surfaces.services.line_markup import Bot, LineMarkup
from app.modules.agent_surfaces.services.channel_lines import reply_ref_of
from app.modules.agent_surfaces.services.sender_handles import (
    group_handle,
    workspace_handle,
)
from app.modules.agent_surfaces.services.verified_surface_identity import (
    resolve_shared_verified_identity,
)
from app.modules.agent_surfaces.services.channel_places import (
    ChannelSeats,
    ChannelSeatsInDatabase,
)
from app.modules.agent_surfaces.services.workspace_channels import (
    WORKSPACE_PLATFORMS,
    note_workspace_channel,
)
from app.modules.channels.contracts import (
    ChannelMessage,
    NewLine,
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
from app.modules.channels.contracts.lines import thread_root_at
from app.modules.contacts.contracts import (
    IdentityStrength,
    find_contact,
    open_contact,
)

logger = get_logger(__name__)


#: How much of a group a run is shown. The same window a Slack run fetches, so a
#: group reads the same to the agent whichever platform it is on.
GROUP_CONTEXT_LINES = 15

#: How many of a group's people go in front of the model. The roster is who the
#: pod knows is there, and a group past this many speakers has outgrown a list.
_MAX_PARTICIPANTS = 20

#: Where a sender's verified identity is one binding for every bot of the
#: platform, so a group line can be matched to it without the installation a
#: Slack or Teams binding is kept under.
_SHARED_BINDING_PLATFORMS = frozenset(
    {SurfacePlatform.TELEGRAM, SurfacePlatform.WHATSAPP}
)


def keeps_group_log(platform: str) -> bool:
    """Whether a run reads this platform's groups from the pod's lines rather
    than from the platform's own history."""
    capabilities = PLATFORM_CAPABILITIES.get(str(platform).upper())
    return capabilities is not None and not capabilities.reads_channel_history


def background_from_log(parsed: ParsedInboundSurfaceEvent) -> bool:
    """Whether this group's background is the pod's lines: a platform with no
    history to read, or a Teams group chat, which is in no team's channels."""
    return keeps_group_log(parsed.platform.value) or (
        parsed.platform is SurfacePlatform.TEAMS and is_group_dm(parsed)
    )


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
        wrote = False
        if parsed.platform in WORKSPACE_PLATFORMS:
            # Routed one by one: its source is made as the bot first hears it.
            wrote = await note_workspace_channel(
                self.groups, self.seats, surfaces, parsed
            )
        if not parsed.message_text.strip():
            return wrote
        known = await self.groups.list_for_channel(
            external_channel_id=parsed.external_channel_id,
            surface_ids=[surface.id for surface in surfaces],
        )
        threaded = parsed.platform in WORKSPACE_PLATFORMS
        for group in known:
            # A Slack or Teams thread is a thread here too.
            root = (
                await thread_root_at(
                    self.uow,
                    source_id=group.id,
                    external_thread_id=parsed.external_thread_id,
                    external_message_id=parsed.external_message_id,
                )
                if threaded
                else None
            )
            said = await LineMarkup(self.uow).read(
                parsed,
                pod_id=group.pod_id,
                channel_id=group.channel_id,
                bot=Bot(surface_id=group.surface_id),
            )
            await post(
                self.uow,
                channel_id=group.channel_id,
                line=NewLine(
                    sender=await self._speaker(group, parsed),
                    text=said.text,
                    parts=said.parts,
                    source_id=group.id,
                    external_message_id=parsed.external_message_id,
                    external_thread_id=parsed.external_thread_id,
                    thread_root_id=root,
                    # What answers this line in place -- a member replying to
                    # it in Lemma -- goes to its thread there.
                    reply_ref=reply_ref_of(parsed),
                ),
                mentions=said.mentions,
                mentioned_agents=said.agents,
            )
        return wrote or bool(known)

    async def _speaker(
        self, group: SurfaceGroup, parsed: ParsedInboundSurfaceEvent
    ) -> Sender:
        """Who said it: the member the pod resolved them to, else a contact."""
        name = parsed.sender_display_name
        # Teams resolves a person by their AAD object id, not the bot id.
        external_id = parsed.sender_aad_object_id or parsed.sender_external_user_id
        if external_id:
            resolved = await self.external_users.resolved_users_by_external_ids(
                platform=group.platform,
                tenant_id=parsed.tenant_id,
                external_user_ids=[external_id],
            )
            user_id = resolved.get(external_id) or await self._verified_user(parsed)
            if (
                user_id is not None
                and user_id
                in await self.membership.pod_members_among(group.pod_id, [user_id])
            ):
                return Sender.member(user_id, display_name=name)
        handle = group_handle(parsed)
        if handle is None:
            return await self._known_colleague(group, parsed, name)
        if not handle.vouched_for:
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

    async def _verified_user(self, parsed: ParsedInboundSurfaceEvent) -> UUID | None:
        """The Lemma user this sender proved to be, where the cache has no row.

        A member who proved their account to the bot -- by the onboarding
        code -- holds a verified identity, and that identity is what a DM
        resolves them by. The group log asked only the
        cache, which a DM fills only on some paths and a phone change empties:
        their next line in a group was written as a new contact's, and a group
        line nobody addressed to the bot never resolves anybody to fill it.
        """
        if parsed.platform not in _SHARED_BINDING_PLATFORMS:
            return None
        resolved = await resolve_shared_verified_identity(self.uow, parsed)
        return resolved.internal_user_id if resolved is not None else None

    async def _known_colleague(
        self, group: SurfaceGroup, parsed: ParsedInboundSurfaceEvent, name: str | None
    ) -> Sender:
        """A Slack colleague the pod already answers as a contact, else nobody named.

        Found, never opened: this runs before anything has resolved the
        sender, and a pod member not recognised yet is not to be made a
        contact. A door opens one once resolution says they are not in the pod
        (``slack_workspace_people``); from then their lines name them.
        """
        handle = workspace_handle(parsed)
        known = (
            await find_contact(
                self.uow, pod_id=group.pod_id, kind=handle.kind, value=handle.value
            )
            if handle is not None
            else None
        )
        if known is None:
            return Sender(kind=SenderKind.CONTACT, display_name=name)
        return Sender.contact(known.id, display_name=name)


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
    #: The lines read, as the pod reads them: what the run keeps a reference
    #: to, and reads again -- for this reader -- whenever its prompt is built.
    heard: tuple[ChannelMessage, ...] = ()


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
    * **A member's run** is shown everything said in the group, what people
      outside the pod wrote marked as theirs: an answer made without what the
      room said is wrong in front of the room. It acts with all of the
      member's access, so what it reads from outside is information, never a
      request -- the authorizer asks the member before such a run acts
      (``core.authorization.outside_text``). Not a note the pod's members left
      one another: the answer goes to the whole group.

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
            # Not a members' note: the answer is posted where everybody in the
            # group reads it. The same rule the background is read back by
            # (``channels.contracts.backgrounds``, MEMBER_IN_GROUP).
            shown = not line.members_only
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
        heard=tuple(lines),
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


async def live_members(
    uow_factory: UnitOfWorkFactory,
    *,
    lines: Sequence[Mapping[str, object]],
    pod_id: UUID,
    platform: str,
    tenant_id: str | None,
    conversation_id: UUID,
    reads: RosterReads = roster_reads,
) -> set[str] | None:
    """Which speakers of a history fetched live (Slack, Teams) hold access here:
    the pod's own answer (``pod_members_in_lines``), since a line says who
    spoke, never who holds access.

    ``None`` when the read fails, which is not "nobody": the caller marks
    every speaker as outside and names no roster, rather than fail the
    member's turn over a best-effort history.
    """
    if not lines:
        return set()
    try:
        async with uow_factory() as uow:
            membership, external_users = reads(uow)
            return await pod_members_in_lines(
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
        return None


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


def marked_for_member_run(
    lines: Sequence[dict[str, object]],
    *,
    verified: Set[str] | None,
    asker_external_id: str | None,
) -> list[dict[str, object]]:
    """A platform's history as a member's run is shown it: every line, each
    marked by whether somebody outside the pod wrote it.

    The same rule ``group_background`` applies to the pod's own log, for the
    platforms whose history is read live (Slack). The mark is the pod's answer
    about who holds access (``live_members``), not the platform's: Slack marks
    only another company's lines, and a colleague here with no Lemma account
    reads exactly like a member. The asker's own lines and an app's are no
    stranger's. Where the pod's answer could not be read (``verified`` is
    ``None``), every other person's line is marked: a stranger left unmarked is
    the mistake that costs, a colleague marked only asks the member before the
    run acts.
    """
    marked: list[dict[str, object]] = []
    for line in lines:
        external_id = _external_id(line)
        somebody_else = not line.get("from_bot") and not (
            asker_external_id and external_id == asker_external_id
        )
        outside = somebody_else and (verified is None or external_id not in verified)
        marked.append({**line, "outside_pod": outside})
    return marked


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
