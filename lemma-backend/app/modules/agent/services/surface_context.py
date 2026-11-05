"""Which chat platform a run talks over, from what agent_surfaces records.

A conversation a surface heard has its place in agent_surfaces' own table
(``agent_surfaces.contracts.conversations``): the platform, the installation,
the platform's channel, thread, user and message ids. A run's tool context
needs nine of those fields; ``RunSession`` reads the place once and this turns
it into them.

A conversation in a channel is one person's across every place of it -- Lemma
as much as WhatsApp or Slack -- so the place is where it was last heard, not
where *this* run was asked. A run a line in Lemma started talks over no
platform (`private_notes.asked_in_lemma`): it gets none of these fields, and so
no platform's guidance, tools, files or progress. Reading the place alone told
a member asking in Lemma that they were on WhatsApp, and sent the widget they
asked for to their WhatsApp chat.

`parse_surface_event_metadata` is imported lazily and its failure is tolerated
on purpose: `agent_surfaces` imports this module's package, so reaching it at
import time would cycle, and event metadata stored in an older shape should
still run with the raw payload rather than not at all.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import TYPE_CHECKING, TypedDict
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.run_state import RunPrivacy

if TYPE_CHECKING:
    from app.modules.agent_surfaces.contracts.conversations import (
        ConversationSurface,
    )


class SurfaceContext(TypedDict):
    """The surface fields of a run's tool context, named and typed.

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
    #: The place's kind and widget, whoever asked the run.
    place_kind: str | None
    on_web_widget: bool


async def conversation_surface(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> ConversationSurface | None:
    """Where agent_surfaces last heard this conversation, if anywhere."""
    from app.modules.agent_surfaces.contracts.conversations import (
        conversation_surface as recorded,
    )

    return await recorded(uow, conversation_id)


async def conversation_surfaces(
    uow: SqlAlchemyUnitOfWork, conversation_ids: Iterable[UUID]
) -> dict[UUID, ConversationSurface]:
    """The same for many conversations; those no surface heard are absent."""
    from app.modules.agent_surfaces.contracts.conversations import (
        conversation_surfaces as recorded,
    )

    return await recorded(uow, conversation_ids)


#: ``surface_kind``'s shape, so a use case can be handed another.
SurfaceKindReader = Callable[[SqlAlchemyUnitOfWork, UUID], Awaitable[str | None]]


async def surface_kind(uow: SqlAlchemyUnitOfWork, conversation_id: UUID) -> str | None:
    """``DM``, ``CHANNEL`` or ``EMAIL``, as routing recorded the conversation's
    place; ``None`` where no surface has heard it."""
    surface = await conversation_surface(uow, conversation_id)
    return surface.conversation_kind if surface is not None else None


class _Place(TypedDict):
    place_kind: str | None
    on_web_widget: bool


def surface_context_for_run(
    surface: ConversationSurface | None, run: RunPrivacy | None
) -> SurfaceContext:
    """The surface fields a run's tool context needs: nulls for a run asked
    in Lemma, or in a conversation no surface has heard -- but the kind of
    place it is, whoever asked."""
    place: _Place = {
        "place_kind": surface.conversation_kind if surface else None,
        "on_web_widget": surface is not None and surface.is_web_widget,
    }
    if surface is None or (run is not None and run.asked_in_lemma):
        return {
            **place,
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
    return {
        **place,
        "surface_id": surface.surface_id,
        "surface_platform": surface.surface_platform,
        "surface_metadata": _parsed_event_metadata(surface.surface_event_metadata),
        "external_channel_id": surface.external_channel_id,
        "external_thread_id": surface.external_thread_id,
        "external_user_id": surface.external_user_id,
        "external_message_id": surface.external_message_id,
        "agent_display_name": surface.agent_display_name,
        "surface_conversation_kind": surface.conversation_kind,
    }


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
