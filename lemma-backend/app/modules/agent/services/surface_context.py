"""Where a conversation came from, read off its metadata.

A conversation that started on Slack, Teams or email carries the surface it
arrived on in `metadata`, and both the runner and the conversation MCP bridge
need the same eight fields out of it before they can build a tool context. They
each had a copy; this is the one they share.

`parse_surface_event_metadata` is imported lazily and its failure is tolerated
on purpose: `agent_surfaces` imports this module's package, so reaching it at
import time would cycle, and a conversation whose stored metadata predates the
current shape should still run with the raw payload rather than not at all.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.value_objects import JsonObject


def surface_context_from_conversation(conversation: Conversation) -> JsonObject:
    """The surface fields a tool context needs, or nulls when there is no surface."""
    metadata = conversation.metadata or {}
    surface_id = metadata.get("surface_id")
    return {
        "surface_id": UUID(str(surface_id)) if surface_id else None,
        "surface_platform": metadata.get("surface_platform"),
        "surface_metadata": _parsed_event_metadata(
            metadata.get("surface_event_metadata")
        ),
        "external_channel_id": metadata.get("external_channel_id"),
        "external_thread_id": metadata.get("external_thread_id"),
        "external_user_id": metadata.get("external_user_id"),
        "external_message_id": metadata.get("external_message_id"),
        "agent_display_name": metadata.get("agent_display_name"),
    }


def _parsed_event_metadata(payload: object) -> object:
    """The typed surface metadata, falling back to the payload as stored."""
    if not isinstance(payload, dict):
        return None
    try:
        from app.composition.agent_surface_runtime import (
            parse_surface_event_metadata,
        )

        return parse_surface_event_metadata(payload)
    except Exception:
        return payload
