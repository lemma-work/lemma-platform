"""What a run is shown of the conversation around it, and who else reads it.

A group run is given the recent lines of its group as background -- the pod's
own log where it keeps one, otherwise the platform's history read live -- and
told who outside the pod reads the chat. An email run is told who a reply-all
reaches. Free functions over a unit-of-work factory, each opening its own short
scope, because `SurfaceTurnStarter` gathers this between platform calls and
holds no connection across them.
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
    keeps_group_log,
    live_participants,
    roster_reads,
)
from app.modules.agent_surfaces.services.surface_inbound_message import (
    fetch_channel_context,
)
from app.modules.channels.contracts import Principal, Reader, Source


@dataclass(frozen=True, slots=True)
class RunBackground:
    """The group as one run is shown it."""

    lines: list[dict[str, object]]
    withheld: int = 0
    audience: Audience | None = None
    #: Everyone the pod knows is in the group, and whether they are in the pod.
    participants: tuple[SurfaceGroupParticipant, ...] = ()

    def to_metadata(self) -> dict[str, object]:
        """The run's message metadata keys for this background, only those set."""
        metadata: dict[str, object] = {}
        if self.lines:
            metadata["channel_context"] = self.lines
        if self.withheld:
            metadata["channel_context_withheld"] = self.withheld
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

    The pod's own log where it keeps one (Telegram, WhatsApp) and it has lines;
    otherwise the platform's history, read live. Either way a member's run is not shown
    what strangers wrote (``group_log``), and is told that they are there.
    """
    group, logged = await _group_background(uow_factory, context)
    # A log with nothing in it yet -- a group first heard with this very
    # message -- says less than the platform does: the message replied to.
    if logged is not None and logged.lines:
        lines = [line.model_dump(mode="json") for line in logged.lines]
        withheld, outside = logged.withheld, list(logged.outside_authors)
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
    # The message being answered was logged on its way in and is already
    # the prompt; repeating it as background shows the agent the question
    # twice. It is the latest line in all but a race, so only that one goes.
    if lines and lines[-1].get("text") == context.event.message_text.strip():
        lines.pop()
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
    return RunBackground(
        lines=lines,
        withheld=withheld,
        audience=audience,
        participants=participants,
    )


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
        or not keeps_group_log(context.platform.value)
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
