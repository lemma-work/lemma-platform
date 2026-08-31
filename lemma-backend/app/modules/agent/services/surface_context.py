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

from typing import TypedDict
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent.domain.entities import Conversation

logger = get_logger(__name__)

#: The metadata keys `_text` narrows. Named once so the drop warning can say
#: which of them was discarded without each call site repeating itself.
_TEXT_KEYS = (
    "surface_platform",
    "external_channel_id",
    "external_thread_id",
    "external_user_id",
    "external_message_id",
    "agent_display_name",
)


class SurfaceContext(TypedDict):
    """The eight surface fields, named and typed.

    A `TypedDict` rather than a plain dict because both callers splat this into
    a `ConversationContext(...)`, and splatting a `dict[str, object]` types
    *every* field of that constructor as `object` -- including the dozen this
    function never supplies. Declaring the keys is what makes the splat as
    checkable as writing them out by hand.
    """

    surface_id: UUID | None
    surface_platform: str | None
    surface_metadata: object | None
    external_channel_id: str | None
    external_thread_id: str | None
    external_user_id: str | None
    external_message_id: str | None
    agent_display_name: str | None


def surface_context_from_conversation(conversation: Conversation) -> SurfaceContext:
    """The surface fields a tool context needs, or nulls when there is no surface."""
    metadata = conversation.metadata or {}
    surface_id = metadata.get("surface_id")
    _warn_about_dropped_text(conversation, metadata)
    return {
        "surface_id": UUID(str(surface_id)) if surface_id else None,
        "surface_platform": _text(metadata.get("surface_platform")),
        "surface_metadata": _parsed_event_metadata(
            metadata.get("surface_event_metadata")
        ),
        "external_channel_id": _text(metadata.get("external_channel_id")),
        "external_thread_id": _text(metadata.get("external_thread_id")),
        "external_user_id": _text(metadata.get("external_user_id")),
        "external_message_id": _text(metadata.get("external_message_id")),
        "agent_display_name": _text(metadata.get("agent_display_name")),
    }


def _text(value: object) -> str | None:
    """A stored string, or None for anything else.

    Every surface writes these already stringified (a Telegram chat id is an
    int on the wire and `str()`-ed by its adapter), so this narrows rather than
    converts. Metadata that somehow holds another type used to reach pydantic
    and fail the whole run at context construction; dropping the one field is
    the smaller loss. `_warn_about_dropped_text` is what keeps it from being a
    silent one.
    """
    return value if isinstance(value, str) else None


def _warn_about_dropped_text(conversation: Conversation, metadata: object) -> None:
    """Say so when `_text` is about to discard a field.

    Once per conversation naming every offender, rather than once per field:
    metadata that holds the wrong type usually holds it for the whole surface,
    and six lines would say the same thing six times.

    This should never fire. If it does, a reply is going to the wrong place or
    nowhere -- an agent with no `external_channel_id` has nothing to answer on
    -- and the field it names is where the surface that wrote it went wrong.
    """
    if not isinstance(metadata, dict):
        return
    dropped = sorted(
        key
        for key in _TEXT_KEYS
        if (value := metadata.get(key)) is not None and not isinstance(value, str)
    )
    if not dropped:
        return
    logger.warning(
        "agent.surface_context.non_text_metadata_dropped.degraded",
        conversation_id=str(conversation.id),
        # Joined, not a list: the log pipeline drops a list-valued field and
        # keeps only its name, so `metadata_fields=[...]` would have reported
        # that something called `metadata_fields` went missing and left the
        # actual names -- the whole point of the line -- out of the record.
        metadata_fields=",".join(dropped),
    )


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
