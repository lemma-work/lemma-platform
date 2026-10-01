"""A connected Slack channel is one of the pod's groups.

Slack channels have always been places a pod's bot answers -- routed, one by
one, when somebody connects them. What they lacked is a row in the pod's list
of groups, so nobody could see them there, and nothing could say which of them
is shared with another company. This keeps that row: made the first time the
bot hears the channel, marked shared the first time Slack says it is.

No lines are logged. Slack keeps the channel's history and the bot reads it
there when asked (``reads_channel_history``); copying it would only be a second
record of somebody else's workspace.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    SurfaceChannelRoute,
    SurfacePlatform,
    is_slack_group_dm,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)


async def note_slack_channel(
    groups: SurfaceGroupRepository,
    surfaces: Sequence[AgentSurfaceEntity],
    parsed: ParsedInboundSurfaceEvent,
) -> bool:
    """Keep the group row of a connected Slack channel current. True if written."""
    if parsed.platform is not SurfacePlatform.SLACK or parsed.is_dm:
        return False
    channel_id = parsed.external_channel_id
    if not channel_id:
        return False
    shared = bool(parsed.metadata.get("is_ext_shared_channel"))
    wrote = False
    for surface in surfaces:
        if surface.surface_type is not SurfacePlatform.SLACK:
            continue
        route = surface.channel_route_for(
            channel_id=channel_id, channel_name=parsed.metadata.get("channel_name")
        )
        if route is None and not is_slack_group_dm(parsed):
            continue
        group = await groups.get(surface_id=surface.id, external_channel_id=channel_id)
        if group is None:
            try:
                group = await groups.ensure(
                    pod_id=surface.pod_id,
                    surface_id=surface.id,
                    platform=SurfacePlatform.SLACK.value,
                    external_channel_id=channel_id,
                    title=slack_group_title(route, parsed),
                )
            except LookupError:
                # Removed as it was made: nothing to keep current this time.
                continue
            wrote = True
        if shared and not group.shared_externally:
            await groups.set_shared_externally(group.id, shared=True)
            wrote = True
    return wrote


def slack_group_title(
    route: SurfaceChannelRoute | None, parsed: ParsedInboundSurfaceEvent
) -> str | None:
    """``#channel`` where the channel's name is known; a group DM has none."""
    if is_slack_group_dm(parsed):
        return "Group DM"
    name = str((route.channel_name if route else None) or "").strip().lstrip("#")
    return f"#{name}" if name else None


def answers_slack_outsider(
    group_shared: bool, parsed: ParsedInboundSurfaceEvent
) -> bool:
    """Whether a Slack sender outside the pod is an outsider to answer.

    Only in a channel shared with another company, and only a sender from that
    company. A colleague in the pod's own workspace who is not in the pod is
    somebody to invite, and gets the private nudge to join instead.
    """
    return group_shared and bool(parsed.metadata.get("sender_is_external"))
