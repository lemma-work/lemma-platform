"""Root adapters between the agent runtime and optional surface platforms.

The agent module owns the execution flow. Surface-specific rendering, delivery,
tools, and metadata parsing are bound here so neither module imports the other's
implementation packages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
    from app.modules.agent.domain.entities import Conversation


def platform_agent_guidance(platform: str | None) -> str:
    from app.modules.agent_surfaces.platforms.platform_capabilities import (
        platform_agent_guidance as build_guidance,
    )

    return build_guidance(platform)


def platform_is_known(platform: str | None) -> bool:
    from app.modules.agent_surfaces.platforms.platform_capabilities import (
        get_platform_capabilities,
    )

    return get_platform_capabilities(platform) is not None


def platform_is_email(platform: str | None) -> bool:
    from app.modules.agent_surfaces.platforms.platform_capabilities import (
        get_platform_capabilities,
    )

    capabilities = get_platform_capabilities(platform)
    return bool(capabilities and capabilities.is_email)


def platform_supports_chat_delivery(platform: str | None) -> bool:
    from app.modules.agent_surfaces.platforms.platform_capabilities import (
        get_platform_capabilities,
    )

    capabilities = get_platform_capabilities(platform)
    return bool(capabilities and not capabilities.is_email)


def voice_note_format(platform: str | None) -> str:
    from app.modules.agent_surfaces.platforms.platform_capabilities import (
        voice_note_format as resolve_format,
    )

    return resolve_format(platform)


def render_attachment_context(
    attachments: list[object], *, platform: str
) -> tuple[str, str]:
    from app.modules.agent_surfaces.platforms.common import (
        attachment_tool_hint,
        render_attachment_prompt_block,
    )

    return (
        render_attachment_prompt_block(
            attachments,
            platform=platform,
            include_hint=False,
        ),
        attachment_tool_hint(platform),
    )


def email_reply_instruction(platform: str) -> str:
    from app.modules.agent_surfaces.platforms.common import (
        email_reply_instruction as build_instruction,
    )

    return build_instruction(platform)


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
