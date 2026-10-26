"""Which chat platform a run talks over, read off its conversation's metadata.

A conversation a surface heard carries the platform it was last written to
from in `metadata`, and both the runner and the conversation MCP bridge need
the same nine fields out of it before they can build a tool context. They each
had a copy; this is the one they share.

A conversation in a channel is one person's across every place of it -- Lemma
as much as WhatsApp or Slack -- so its metadata says where it was last heard,
not where *this* run was asked. A run a line in Lemma started talks over no
platform (`private_notes.asked_in_lemma`): it gets none of these fields, and so
no platform's guidance, tools, files or progress. Reading the conversation
alone told a member asking in Lemma that they were on WhatsApp, and sent the
widget they asked for to their WhatsApp chat.

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
from app.modules.agent.domain.run_state import RunPrivacy

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
    "conversation_kind",
)


class SurfaceContext(TypedDict):
    """The nine surface fields, named and typed.

    A `TypedDict` rather than a plain dict because both callers splat this into
    a `ToolContext(...)`, and splatting a `dict[str, object]` types
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
    #: ``DM``, ``CHANNEL`` or ``EMAIL``, as routing recorded it.
    surface_conversation_kind: str | None


def _no_surface() -> SurfaceContext:
    """A run that talks over no platform."""
    return {
        "surface_id": None,
        "surface_platform": None,
        "surface_metadata": None,
        "external_channel_id": None,
        "external_thread_id": None,
        "external_user_id": None,
        "external_message_id": None,
        "agent_display_name": None,
        "surface_conversation_kind": None,
    }


def surface_context_for_run(
    conversation: Conversation, run: RunPrivacy | None
) -> SurfaceContext:
    """The surface fields a run's tool context needs: nulls for a run asked
    in Lemma, or in a conversation no surface has heard."""
    if run is not None and run.asked_in_lemma:
        return _no_surface()
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
        "surface_conversation_kind": surface_conversation_kind(conversation),
    }


def surface_conversation_kind(conversation: Conversation) -> str | None:
    """``DM``, ``CHANNEL`` or ``EMAIL`` as routing recorded it, else None."""
    metadata = conversation.metadata if isinstance(conversation.metadata, dict) else {}
    return _text(metadata.get("conversation_kind"))


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
    """The typed surface metadata, falling back to the payload as stored.

    Narrow on ``ValidationError`` now that the operation says what it returns:
    a payload written by an older shape should still reach the caller, but a
    bug in the parse should not read as one.
    """
    if not isinstance(payload, dict):
        return None
    from pydantic import ValidationError

    from app.modules.agent_surfaces.contracts.egress import (
        parse_surface_event_metadata,
    )

    try:
        return parse_surface_event_metadata(payload)
    except ValidationError:
        return payload
