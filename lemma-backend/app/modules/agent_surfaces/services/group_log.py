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

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID


from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
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
from app.modules.agent_surfaces.services.link_nudges import nudge_lookalikes
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
        known = await find_contact(
            self.uow, pod_id=group.pod_id, kind=handle.kind, value=handle.value
        )
        contact = await open_contact(
            self.uow,
            pod_id=group.pod_id,
            kind=handle.kind,
            value=handle.value,
            strength=IdentityStrength.CHANNEL,
            display_name=name,
        )
        if known is None:
            # Somebody new: a member who shares their name is asked if it is
            # them, since Lemma cannot know an unlinked account is a member's.
            await nudge_lookalikes(
                self.uow, group=group, contact_id=contact.id, name=name
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
