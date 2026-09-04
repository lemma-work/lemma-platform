"""What a run needs to know about the platform it is talking on.

A lookup over a platform string and the settings that bound a surface
conversation: no I/O, no database, no service. `agent` asked all of it through
`app/composition/agent_surface_runtime.py`, which put a third module path
between the question and the table holding the answer -- and counted, at nine
import edges, as `agent` depending on `agent_surfaces` for reasons
indistinguishable from the deliveries that genuinely do run both ways.

A submodule rather than `contracts/__init__`, for the reason its siblings in
`schedule`, `usage` and `workspace` are: `__init__` is what anything wanting any
contract at all imports, and importing anything under `platforms` runs
`platforms/__init__`, which loads every platform SDK the transports need.
"""

from __future__ import annotations

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.platforms.common import (
    attachment_tool_hint,
    email_reply_instruction,
    render_attachment_prompt_block,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    DeliveryCardinality,
    get_platform_capabilities,
    platform_agent_guidance,
    voice_note_format,
)


def platform_is_known(platform: str | None) -> bool:
    """Does the registry have an entry for this platform?"""
    return get_platform_capabilities(platform) is not None


def platform_delivers_one_reply(platform: str | None) -> bool:
    """Does a run on this platform get one composed reply rather than messages?"""
    capabilities = get_platform_capabilities(platform)
    return bool(
        capabilities and capabilities.delivery_cardinality is DeliveryCardinality.ONE
    )


def platform_supports_chat_delivery(platform: str | None) -> bool:
    """Can something be sent the moment it is ready, rather than held for the reply?"""
    capabilities = get_platform_capabilities(platform)
    return bool(capabilities and not capabilities.is_email)


def render_attachment_context(
    attachments: list[object], *, platform: str
) -> tuple[str, str | None]:
    """What the prompt should say about files that arrived with a message.

    The listing, and the hint naming the download tool that can fetch them --
    two blocks rather than one because the caller places them separately, and
    the hint is ``None`` on a platform with no such tool.
    """
    return (
        render_attachment_prompt_block(attachments, platform=platform),
        attachment_tool_hint(platform),
    )


def surface_history_limits() -> tuple[int, int]:
    """Runtime history bounds for a surface conversation.

    ``(max_messages, window_hours)``; either at or below zero disables that
    half of the bound.
    """
    return (
        surface_settings.surface_runtime_history_max_messages,
        surface_settings.surface_runtime_history_window_hours,
    )


__all__ = [
    "email_reply_instruction",
    "platform_agent_guidance",
    "platform_delivers_one_reply",
    "platform_is_known",
    "platform_supports_chat_delivery",
    "render_attachment_context",
    "surface_history_limits",
    "voice_note_format",
]
