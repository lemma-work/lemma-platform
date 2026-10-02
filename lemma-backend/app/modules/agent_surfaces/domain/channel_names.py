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

    Slack and Teams send only the id -- ``C07AB12CD`` names nothing a reader
    recognises -- and the surface's own routes are the one place a name for it
    already exists, put there when somebody configured the channel. A group
    nobody routes can still carry its name: Telegram sends the title with every
    message, and a group the pod keeps a record of is named from that record
    (``chat_title``, see ``services/outsiders``). So this is a lookup, never a
    platform call: naming the place a message came from must not be able to
    cost a round trip on the ingress path.

    ``None`` for a DM (there is no channel to name) and for a channel nobody
    named anywhere, where the id is all anyone has.
    """
    if parsed.is_dm:
        return None
    channel_id = str(parsed.external_channel_id or "").strip()
    if not channel_id:
        return None
    for route in surface.config.channels:
        if str(route.channel_id or "").strip() == channel_id:
            return str(route.channel_name or "").strip() or None
    title = parsed.metadata.get("chat_title")
    return (title.strip() or None) if isinstance(title, str) else None


__all__ = ["configured_channel_name"]
