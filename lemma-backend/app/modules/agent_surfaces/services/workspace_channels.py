"""A connected Slack or Teams channel, or a group chat, is one of the pod's channels.

Slack and Teams channels have always been places a pod's bot answers -- routed,
one by one, when somebody connects them -- and a group DM (a Slack ``mpim``, a
Teams group chat) is one somebody started with the bot in it. Each is a source
of a GROUP channel in Lemma: made the first time the bot hears it, marked
shared the first time Slack says another company is in it.

What is said there is logged as the channel's lines (``group_log``), so the
pod's members read it in Lemma beside everything else. A run still reads a
Slack or Teams channel's history from the platform, which holds all of it.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    SurfaceChannelRoute,
    SurfacePlatform,
    is_group_dm,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.domain.external_accounts import account_of
from app.modules.agent_surfaces.domain.place_privacy import PlacePrivacy, said_privacy
from app.modules.agent_surfaces.services.channel_places import ChannelSeats, is_private
from app.modules.channels.contracts import GroupHome, Place, SourceKind


#: Platforms whose channels are routed one by one, and whose group DMs need none.
WORKSPACE_PLATFORMS = frozenset({SurfacePlatform.SLACK, SurfacePlatform.TEAMS})


async def note_workspace_channel(
    groups: SurfaceGroupRepository,
    seats: ChannelSeats,
    surfaces: Sequence[AgentSurfaceEntity],
    parsed: ParsedInboundSurfaceEvent,
) -> bool:
    """Keep the source of a connected Slack or Teams place current. True if written."""
    if parsed.platform not in WORKSPACE_PLATFORMS or parsed.is_dm:
        return False
    channel_id = parsed.external_channel_id
    if not channel_id:
        return False
    shared = bool(parsed.metadata.get("is_ext_shared_channel"))
    wrote = False
    for surface in surfaces:
        if surface.surface_type is not parsed.platform:
            continue
        route = surface.channel_route_for(
            channel_id=channel_id, channel_name=parsed.metadata.get("channel_name")
        )
        if route is None and not is_group_dm(parsed):
            continue
        known = await seats.places_hearing(
            platform=parsed.platform.value,
            external_channel_id=channel_id,
            surface_ids=[surface.id],
        )
        if known:
            group = known[0]
            if not group.is_private and _now_private(parsed):
                # Converted since it was made (Slack names the new type on
                # every message): its mirror follows, one way only.
                wrote = await groups.became_private(group.id) or wrote
        else:
            title = workspace_channel_title(route, parsed)
            try:
                group = await seats.source_for(
                    pod_id=surface.pod_id,
                    place=Place(
                        surface_id=surface.id,
                        platform=parsed.platform.value,
                        account=account_of(surface),
                        kind=SourceKind.CHAT,
                        external_channel_id=channel_id,
                        external_user_id=None,
                        title=title,
                        is_private=is_private(surface, parsed),
                    ),
                    home=GroupHome(title=title),
                )
            except LookupError:
                # Removed as it was made: nothing to keep current this time.
                continue
            wrote = True
        if shared and not group.shared_externally:
            await groups.set_shared_externally(group.id, shared=True)
            wrote = True
    return wrote


def _now_private(parsed: ParsedInboundSurfaceEvent) -> bool:
    """The platform itself said this place is private -- not merely unsaid."""
    return said_privacy(parsed.platform, parsed.metadata) is PlacePrivacy.PRIVATE


def workspace_channel_title(
    route: SurfaceChannelRoute | None, parsed: ParsedInboundSurfaceEvent
) -> str | None:
    """``#channel`` where the channel's name is known; a group DM has none."""
    if is_group_dm(parsed):
        return "Group chat" if parsed.platform is SurfacePlatform.TEAMS else "Group DM"
    name = str((route.channel_name if route else None) or "").strip().lstrip("#")
    return f"#{name}" if name else None


def answers_slack_outsider(
    group_shared: bool, parsed: ParsedInboundSurfaceEvent
) -> bool:
    """Whether a Slack sender outside the pod is the other company's outsider.

    Only in a channel shared with another company, and only a sender from that
    company. A colleague in the pod's own workspace who is not in the pod is
    somebody to invite, and gets the private nudge to join instead -- unless
    the pod's organization holds the workspace, which the door asks
    separately (``slack_workspace_people``): then they are its people, and
    answered as contacts.
    """
    return group_shared and bool(parsed.metadata.get("sender_is_external"))
