"""A message heard in a connected channel fires the schedules watching it.

The Slack connector's triggers -- a message in a channel, a reply in a thread,
the app mentioned -- have no webhook of their own: a Slack app sends every
event to one URL, the pod's bot's, and the bot already hears what is said in
the channels it was connected to. So a heard message is handed to `schedule`
right after the group log writes it, and the runs it fires commit with that
line (``group_ingress``).

**Whose schedules.** Only those in pods that connected this channel to a bot
that heard it: a place on the channel, among the surfaces whose app the
delivery's signature verified, in the workspace the delivery names. A channel
id in a schedule's config is the author's to type, so it decides which
channel a schedule watches, never which pods may hear one.

**Never the bot's own words.** A message an app posted -- any app, a pod's
own functions answering through the Slack connector among them -- carries a
``bot_id`` and is dropped in the request (``inbound_classifier``) before it is
stored, so a schedule that posts to the channel it watches does not start
itself again.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.slack.triggers import slack_trigger_events
from app.modules.agent_surfaces.services.channel_places import ChannelSeats
from app.modules.schedule.contracts.heard_events import (
    NormalizedWebhook,
    fire_heard_event,
)

#: `fire_heard_event`'s shape, as the seam a test stands in for.
FireHeardEvent = Callable[
    [SqlAlchemyUnitOfWork, NormalizedWebhook], Awaitable[list[UUID]]
]


async def fire_channel_triggers(
    uow: SqlAlchemyUnitOfWork,
    *,
    seats: ChannelSeats,
    surfaces: Sequence[AgentSurfaceEntity],
    parsed: ParsedInboundSurfaceEvent,
    fire: FireHeardEvent = fire_heard_event,
) -> bool:
    """Fire the trigger schedules this group message matches. True if any fired.

    Writes on ``uow`` and does no I/O beyond it, so the caller commits the
    runs with whatever it wrote beside them.
    """
    if parsed.platform is not SurfacePlatform.SLACK or parsed.is_dm:
        return False
    pod_ids = await _pods_hearing(seats, surfaces, parsed)
    if not pod_ids:
        return False
    fired = False
    for event in slack_trigger_events(parsed, pod_ids=pod_ids):
        fired = bool(await fire(uow, event)) or fired
    return fired


async def _pods_hearing(
    seats: ChannelSeats,
    surfaces: Sequence[AgentSurfaceEntity],
    parsed: ParsedInboundSurfaceEvent,
) -> frozenset[str]:
    """The pods with a place on this channel, among the surfaces that heard it.

    The surfaces are the delivery's verified receivers; held to the workspace
    the delivery names as well, because a place is looked up by channel id and
    nothing promises a channel id is unique across workspaces.
    """
    channel_id = parsed.external_channel_id
    workspace = parsed.tenant_id
    if not channel_id or not workspace:
        return frozenset()
    heard_by = [
        surface.id
        for surface in surfaces
        if surface.surface_type is SurfacePlatform.SLACK
        and surface.external_workspace_id == workspace
    ]
    if not heard_by:
        return frozenset()
    places = await seats.places_hearing(
        platform=SurfacePlatform.SLACK.value,
        external_channel_id=channel_id,
        surface_ids=heard_by,
    )
    return frozenset(str(place.pod_id) for place in places)


__all__ = ["fire_channel_triggers"]
