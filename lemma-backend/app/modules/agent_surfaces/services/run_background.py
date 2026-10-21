"""What a run is shown of the conversation around it, and who else reads it.

A group run is given the recent lines of its group as background -- the pod's
own log where it keeps one, otherwise the platform's history read live -- and
told who outside the pod reads the chat. An email run is told who a reply-all
reaches. Free functions over a unit-of-work factory, each opening its own short
scope, because `SurfaceTurnStarter` gathers this between platform calls and
holds no connection across them.

The background is kept as which lines, never what they said
(``channels.contracts.backgrounds``): the run reads them from the timeline as
its prompt is built, as its reader may read them then. A platform's history
read live is matched to the lines the pod heard at that place; only what the
pod never heard -- said before the bot was there -- is kept as the platform
gave it.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.credentials import SurfaceCredentials

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.domain.adapter_port import SurfacePlatformAdapterPort
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.groups import SurfaceGroup
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.models import SurfaceGroupParticipant
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (  # noqa: E501
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.adapters.user_directory_adapter import (
    IdentityUserDirectoryAdapter,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.platforms.resend.email_recipients import (
    reply_all_recipients,
)
from app.modules.agent_surfaces.services.group_audience import (
    Audience,
    chat_audience,
    email_audience,
    outside_names,
)
from app.modules.agent_surfaces.services.group_log import (
    GroupBackground,
    RosterReads,
    for_member_run,
    group_background,
    group_source,
    background_from_log,
    live_participants,
    roster_reads,
)
from app.modules.agent_surfaces.services.surface_inbound_message import (
    fetch_channel_context,
)
from app.modules.channels.contracts import ChannelMessage, Principal, Reader, Source
from app.modules.channels.contracts.backgrounds import (
    BACKGROUND_KEY,
    BackgroundItem,
    BackgroundRefs,
    BackgroundView,
    heard_at,
    refs_of,
)


@dataclass(frozen=True, slots=True)
class RunBackground:
    """The group as one run is shown it."""

    #: The lines this run is shown, by reference.
    refs: BackgroundRefs | None = None
    audience: Audience | None = None
    #: Everyone the pod knows is in the group, and whether they are in the pod.
    participants: tuple[SurfaceGroupParticipant, ...] = ()

    def to_metadata(self) -> dict[str, object]:
        """The run's message metadata keys for this background, only those set."""
        metadata: dict[str, object] = {}
        if self.refs is not None and (self.refs.items or self.refs.withheld):
            metadata[BACKGROUND_KEY] = self.refs.model_dump(mode="json")
        if self.participants:
            metadata["channel_participants"] = [
                participant.model_dump(mode="json") for participant in self.participants
            ]
        if self.audience is not None:
            metadata["outside_audience"] = self.audience.to_metadata()
        return metadata


async def run_background(
    uow_factory: UnitOfWorkFactory,
    context: SurfaceChatContext,
    *,
    adapter: SurfacePlatformAdapterPort,
    credentials: SurfaceCredentials,
    roster_reads: RosterReads = roster_reads,
) -> RunBackground:
    """What this run is shown of the group, and who outside the pod reads it.

    The pod's own log where it keeps one (Telegram, WhatsApp, a Teams group
    chat) and it has lines;
    otherwise the platform's history, read live. Either way a member's run is not shown
    what strangers wrote (``group_log``), and is told that they are there.
    """
    group, logged = await _group_background(uow_factory, context)
    # A log with nothing in it yet -- a group first heard with this very
    # message -- says less than the platform does: the message replied to.
    if logged is not None and logged.lines:
        refs = _logged_refs(context, logged.heard, group)
        outside = list(logged.outside_authors)
        participants = logged.participants
    else:
        lines = await fetch_channel_context(
            adapter=adapter, context=context, credentials=credentials
        )
        outside = outside_names(lines)
        withheld = 0
        # A stranger's run acts as nobody, so it is not handed a roster of
        # who holds access to this pod -- only a member's run is.
        participants = (
            ()
            if context.audience.answers_outsiders
            else await _participants_in_live_group(
                uow_factory, context, lines, reads=roster_reads
            )
        )
        if not context.audience.answers_outsiders:
            lines, withheld = for_member_run(lines)
        refs = await _platform_refs(uow_factory, context, lines, withheld=withheld)
    audience = (
        None
        if context.audience.answers_outsiders
        else chat_audience(
            platform=context.platform,
            group=group,
            parsed=context.event,
            outside_authors=outside,
        )
    )
    return RunBackground(refs=refs, audience=audience, participants=participants)


def _logged_refs(
    context: SurfaceChatContext,
    heard: Sequence[ChannelMessage],
    group: SurfaceGroup | None,
) -> BackgroundRefs | None:
    """The pod's own log of the group, as this run's reader reads it."""
    if group is None or context.pod_id is None:
        return None
    # The message being answered was logged on its way in and is already the
    # prompt; repeating it as background shows the agent the question twice.
    answered = context.event.external_message_id
    lines = [
        line for line in heard if not answered or line.external_message_id != answered
    ]
    if lines and lines[-1].text == context.event.message_text.strip():
        lines.pop()
    contact_id = context.audience.contact_id
    if contact_id is not None:
        view, source_id = BackgroundView.CONTACT, group.id
    elif context.audience.answers_outsiders:
        return None
    else:
        view, source_id = BackgroundView.MEMBER_IN_GROUP, None
    return BackgroundRefs(
        pod_id=context.pod_id,
        channel_id=group.channel_id,
        view=view,
        contact_id=contact_id,
        source_id=source_id,
        agent_name=context.agent_display_name,
        items=refs_of(lines),
    )


async def _platform_refs(
    uow_factory: UnitOfWorkFactory,
    context: SurfaceChatContext,
    lines: list[dict[str, object]],
    *,
    withheld: int,
) -> BackgroundRefs | None:
    """A platform's history, read live: what the pod heard of it by reference,
    the rest as the platform gave it."""
    if lines and lines[-1].get("text") == context.event.message_text.strip():
        lines = lines[:-1]
    if context.pod_id is None or not (lines or withheld):
        return None
    group = await _known_group(uow_factory, context)
    heard: dict[str, BackgroundItem] = {}
    if group is not None:
        async with uow_factory() as uow:
            heard = await heard_at(
                uow,
                source_id=group.id,
                external_message_ids=[str(line.get("ts") or "") for line in lines],
            )
    items: list[BackgroundItem] = []
    for line in lines:
        outside = bool(line.get("outside_pod"))
        ref = heard.get(str(line.get("ts") or ""))
        if ref is not None:
            items.append(ref.model_copy(update={"outside_pod": outside}))
            continue
        items.append(
            BackgroundItem(
                author=_text(line.get("author")),
                text=_text(line.get("text")),
                ts=_text(line.get("ts")),
                outside_pod=outside,
            )
        )
    return BackgroundRefs(
        pod_id=context.pod_id,
        channel_id=group.channel_id if group is not None else None,
        view=BackgroundView.PLATFORM,
        withheld=withheld,
        items=tuple(items),
    )


def _text(value: object) -> str | None:
    return str(value) if value is not None else None


async def _participants_in_live_group(
    uow_factory: UnitOfWorkFactory,
    context: SurfaceChatContext,
    lines: Sequence[Mapping[str, object]],
    *,
    reads: RosterReads,
) -> tuple[SurfaceGroupParticipant, ...]:
    """Who the pod can vouch for among a fetched window's speakers."""
    if context.pod_id is None:
        return ()
    return await live_participants(
        uow_factory,
        lines=lines,
        pod_id=context.pod_id,
        platform=context.platform.value,
        tenant_id=context.event.tenant_id,
        asker_external_id=context.message_external_user_id,
        conversation_id=context.conversation_id,
        reads=reads,
    )


async def _known_group(
    uow_factory: UnitOfWorkFactory, context: SurfaceChatContext
) -> SurfaceGroup | None:
    """The group the pod knows this chat as, if it knows it."""
    channel_id = context.event.external_channel_id
    if context.surface_id is None or not channel_id:
        return None
    async with uow_factory() as uow:
        return await SurfaceGroupRepository(uow.session).get(
            surface_id=context.surface_id, external_channel_id=channel_id
        )


async def _group_background(
    uow_factory: UnitOfWorkFactory, context: SurfaceChatContext
) -> tuple[SurfaceGroup | None, GroupBackground | None]:
    """The group the pod keeps a log for, and that log; else nothing.

    Nothing where the platform can read its history (Slack, Teams) or the
    group is not one the pod keeps a log for -- the caller then asks the
    platform, as it always has, and reads a shared Slack channel off the
    event itself.
    """
    channel_id = context.event.external_channel_id
    if (
        context.surface_id is None
        or not channel_id
        or not background_from_log(context.event)
    ):
        return None, None
    async with uow_factory() as uow:
        group = await SurfaceGroupRepository(uow.session).get(
            surface_id=context.surface_id, external_channel_id=channel_id
        )
        if group is None:
            return None, None
        source = await group_source(uow, group)
        if source is None:
            return None, None
        logged = await group_background(
            uow,
            group=group,
            source=source,
            membership=SqlAlchemySurfaceRoutingResolutionAdapter(uow),
            agent_display_name=context.agent_display_name,
            reader=_reader(context, source),
            asker=_asker(context),
        )
    return group, logged


def _reader(context: SurfaceChatContext, source: Source) -> Reader | None:
    """Whom this run reads the group as: its contact, a member, or nobody."""
    audience = context.audience
    if audience.contact_id is not None:
        return Reader.contact(audience.contact_id, source_id=source.id)
    return None if audience.answers_outsiders else Reader.member()


def _asker(context: SurfaceChatContext) -> Principal:
    if context.audience.contact_id is not None:
        return Principal.contact(context.audience.contact_id)
    return Principal.member(context.message_user_id)


async def reply_all_audience(
    uow_factory: UnitOfWorkFactory, context: SurfaceChatContext
) -> Audience | None:
    """Who outside the pod a reply on this email thread also reaches."""
    if context.platform is not SurfacePlatform.RESEND or context.pod_id is None:
        return None
    recipients = reply_all_recipients(context.event)
    if not recipients:
        return None
    pod_id = context.pod_id
    async with uow_factory() as uow:
        people = IdentityUserDirectoryAdapter(uow)
        membership = SqlAlchemySurfaceRoutingResolutionAdapter(uow)

        async def is_member(address: str) -> bool:
            user_id = await people.user_id_by_email(address)
            return user_id is not None and pod_id in set(
                await membership.get_user_pod_ids(user_id)
            )

        return await email_audience(recipients=recipients, is_member=is_member)
