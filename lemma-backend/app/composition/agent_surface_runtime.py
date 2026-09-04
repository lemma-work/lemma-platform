"""Root adapters between the agent runtime and optional surface platforms.

The agent module owns the execution flow. Delivering something to a surface,
building its toolsets and parsing its event metadata are bound here so neither
module imports the other's implementation packages. What a platform *is* -- the
capability lookups and the prompt text derived from them -- is not here: it is
published by `agent_surfaces.contracts.platforms`, which `agent` imports where
it asks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
    from app.modules.agent.domain.entities import Conversation


def hold_display_for_one_reply(conversation_id, path: str) -> bool:
    """Keep a displayed pod file until this surface's single reply goes out."""
    from app.modules.agent_surfaces.services.pending_envelope import (
        remember_display_path,
    )

    return remember_display_path(conversation_id, path)


def parse_surface_event_metadata(payload: dict[str, object]) -> object:
    from pydantic import TypeAdapter

    from app.modules.agent_surfaces.domain.surface_event_metadata import (
        SurfaceEventMetadata,
    )

    return TypeAdapter(SurfaceEventMetadata).validate_python(payload)


async def build_surface_toolsets(
    uow_factory: "UnitOfWorkFactory",
    conversation: "Conversation",
) -> list[object]:
    from app.modules.agent_surfaces.infrastructure.adapters.platform_tool_factory import (
        SurfacePlatformToolFactory,
    )

    return await SurfacePlatformToolFactory(uow_factory).build_toolsets(
        conversation=conversation
    )


async def deliver_display_resource(
    *,
    conversation_id: UUID,
    request: object,
    tool_call_id: str | None,
    tool_output: object,
) -> None:
    from app.modules.agent_surfaces.services.surface_display_delivery import (
        deliver_display_resource_to_surface,
    )

    await deliver_display_resource_to_surface(
        conversation_id=conversation_id,
        request=request,
        tool_call_id=tool_call_id,
        tool_output=tool_output,
    )


async def deliver_voice_note(*, conversation_id: UUID, file_path: str) -> bool:
    from app.modules.agent_surfaces.services.surface_display_delivery import (
        deliver_voice_note_to_surface,
    )

    return await deliver_voice_note_to_surface(
        conversation_id=conversation_id,
        file_path=file_path,
    )


def build_progress_observer(*, uow_factory, service_factory):
    from app.modules.agent_surfaces.services.progress_observer import (
        SurfaceAgentRunProgressObserver,
    )

    return SurfaceAgentRunProgressObserver(
        uow_factory=uow_factory,
        service_factory=service_factory,
    )
