"""What an agent run sends out to the chat surface it is answering on.

Six operations, replacing `app/composition/agent_surface_runtime.py`. That file
existed because `agent` importing `agent_surfaces` would have closed a loop; the
loop is cut (see `contracts/notifications.py`), so this is an ordinary contract
and can say what it takes and returns. Every signature on the shim was `object`
or `list[object]` -- not because the types were unknown, but because naming them
in the composition root would have put a third module's paths into `agent`'s
build. `agent/contracts` publishes `Conversation` and `DisplayResourceRequest`,
so they are named here.

The direction is one-way: `agent` decides what to send and this delivers it.
What a platform *is* -- the capability lookups and the prompt text derived from
them -- is `contracts/platforms.py`.

A submodule rather than `contracts/__init__`, which is a leaf: these reach the
service and adapter layers.
"""

from __future__ import annotations

from typing import Mapping
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.contracts import Conversation, DisplayResourceRequest
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    SurfaceEventMetadata,
)


def hold_display_for_one_reply(conversation_id: UUID, path: str) -> bool:
    """Keep a displayed pod file until this surface's single reply goes out."""
    from app.modules.agent_surfaces.services.pending_envelope import (
        remember_display_path,
    )

    return remember_display_path(conversation_id, path)


def parse_surface_event_metadata(
    payload: Mapping[str, object],
) -> SurfaceEventMetadata:
    """The surface metadata a conversation was started with, typed."""
    from pydantic import TypeAdapter

    return TypeAdapter(SurfaceEventMetadata).validate_python(payload)


async def build_surface_toolsets(
    uow_factory: UnitOfWorkFactory,
    conversation: Conversation,
) -> list[object]:
    """The tools this conversation's platform adds to the run.

    ``list[object]`` because the elements are `pydantic_ai` toolsets, which the
    caller only ever extends its own list with.
    """
    from app.modules.agent_surfaces.infrastructure.adapters.platform_tool_factory import (
        SurfacePlatformToolFactory,
    )

    return await SurfacePlatformToolFactory(uow_factory).build_toolsets(
        conversation=conversation
    )


async def deliver_display_resource(
    *,
    conversation_id: UUID,
    request: DisplayResourceRequest,
    tool_call_id: str | None,
    tool_output: object | None,
) -> bool:
    """Show a resource on the conversation's surface. False when it has none.

    Never raises: delivery is best-effort and must not abort the run.
    """
    from app.modules.agent_surfaces.services.surface_display_delivery import (
        deliver_display_resource_to_surface,
    )

    return await deliver_display_resource_to_surface(
        conversation_id=conversation_id,
        request=request,
        tool_call_id=tool_call_id,
        tool_output=tool_output,
    )


async def deliver_voice_note(*, conversation_id: UUID, file_path: str) -> bool:
    """Send a spoken reply as a voice note. False when the surface takes none."""
    from app.modules.agent_surfaces.services.surface_display_delivery import (
        deliver_voice_note_to_surface,
    )

    return await deliver_voice_note_to_surface(
        conversation_id=conversation_id,
        file_path=file_path,
    )


def build_progress_observer(*, uow_factory: UnitOfWorkFactory, service_factory):
    """An observer that streams a run's progress to the surface watching it."""
    from app.modules.agent_surfaces.services.progress_observer import (
        SurfaceAgentRunProgressObserver,
    )

    return SurfaceAgentRunProgressObserver(
        uow_factory=uow_factory,
        service_factory=service_factory,
    )


__all__ = [
    "build_progress_observer",
    "build_surface_toolsets",
    "deliver_display_resource",
    "deliver_voice_note",
    "hold_display_for_one_reply",
    "parse_surface_event_metadata",
]
