"""Naming the place a message arrived in.

Its own module rather than a function on ``entities``: that file is already at
the size limit, and this is a distinct question anyway. An entity says what a
channel *is*; this answers what to call one in front of a reader.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
)


def configured_channel_name(
    surface: AgentSurfaceEntity,
    parsed: ParsedInboundSurfaceEvent,
) -> str | None:
    """The human name of the channel a message arrived in, when one is known.

    Only the id reaches us on the wire -- ``C07AB12CD`` names nothing a reader
    recognises -- and the surface's own routes are the one place a name for it
    already exists, put there when somebody configured the channel. So this is a
    dict lookup, never a platform call: naming the place a message came from must
    not be able to cost a round trip on the ingress path.

    ``None`` for a DM (there is no channel to name) and for a channel nobody
    routed, where the id is all anyone has.
    """
    if parsed.is_dm:
        return None
    channel_id = str(parsed.external_channel_id or "").strip()
    if not channel_id:
        return None
    for route in surface.config.channels:
        if str(route.channel_id or "").strip() == channel_id:
            return str(route.channel_name or "").strip() or None
    return None


__all__ = ["configured_channel_name"]
