"""What a run needs to know about the platform it is talking on.

A lookup over a platform string: no I/O, no database, no service. `agent` asked all of it through
`app/composition/agent_surface_runtime.py`, which put a third module path
between the question and the table holding the answer -- and counted, at nine
import edges, as `agent` depending on `agent_surfaces` for reasons
indistinguishable from the deliveries that genuinely do run both ways.

Facts only. What to *say* to the model about them is prompt text, and prompt
text belongs to `agent` (`agent/domain/surface_prompts.py`): a surface is
ingress, agent, egress, and it does not get to decide how the agent is briefed.

A submodule rather than `contracts/__init__`, for the reason its siblings in
`schedule`, `usage` and `workspace` are: `__init__` is what anything wanting any
contract at all imports, and importing anything under `platforms` runs
`platforms/__init__`, which loads every platform SDK the transports need.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.agent_surfaces.platforms.platform_capabilities import (
    DeliveryCardinality,
    PlatformCapabilities,
    ProgressStyle,
    get_platform_capabilities,
    voice_note_format,
)


@dataclass(frozen=True, slots=True)
class PlatformFacts:
    """Stable, typed facts about one surface platform, for the agent to brief itself on.

    Never changes mid-conversation, so whatever the agent derives from it is safe
    in the cached system-prompt prefix. A snapshot of `PlatformCapabilities`
    rather than the registry entry itself, so the agent depends on this shape and
    not on how the surface module happens to store its table.
    """

    #: Canonical upper-case key, e.g. ``"SLACK"``.
    platform: str
    #: Human label, e.g. ``"Microsoft Teams"``.
    display_name: str
    is_email: bool
    #: ``ONE`` when every envelope of a run is composed into a single reply.
    delivery_cardinality: DeliveryCardinality
    #: Hours after the person's last message during which a free-form reply is
    #: allowed; ``None`` where there is no window.
    reply_window_hours: int | None
    progress_style: ProgressStyle
    #: Rough per-message length budget.
    soft_char_limit: int
    #: One line of platform-specific formatting advice, meant to be quoted.
    formatting_style: str
    supports_native_choices: bool
    supports_native_files: bool
    #: ``say`` lands as a real voice-note bubble (otherwise a file or a link).
    supports_native_voice: bool
    #: Can be @-mentioned in a group, so other people's words reach the agent.
    is_channel_capable: bool
    #: Can fetch the conversation around a mention, not just the replied-to text.
    reads_channel_history: bool
    #: Inline attachment ceiling in MB, as the number to quote to the agent.
    inline_mb_cap: int
    #: Phrase for media kinds capped below ``inline_mb_cap``; ``None`` if uniform.
    media_cap_note: str | None
    #: A platform-wide webhook arrives on Lemma's own shared bot here.
    has_shared_system_bot: bool

    @property
    def shows_live_progress(self) -> bool:
        return self.progress_style is not ProgressStyle.NONE


def _facts(capabilities: PlatformCapabilities) -> PlatformFacts:
    return PlatformFacts(
        platform=capabilities.platform,
        display_name=capabilities.display_name,
        is_email=capabilities.is_email,
        delivery_cardinality=capabilities.delivery_cardinality,
        reply_window_hours=capabilities.reply_window_hours,
        progress_style=capabilities.progress_style,
        soft_char_limit=capabilities.soft_char_limit,
        formatting_style=capabilities.formatting_style,
        supports_native_choices=capabilities.supports_native_choices,
        supports_native_files=capabilities.supports_native_files,
        supports_native_voice=capabilities.supports_native_voice,
        is_channel_capable=capabilities.is_channel_capable,
        reads_channel_history=capabilities.reads_channel_history,
        inline_mb_cap=capabilities.inline_mb_cap,
        media_cap_note=capabilities.media_cap_note,
        has_shared_system_bot=capabilities.has_shared_system_bot,
    )


def platform_facts(platform: str | None) -> PlatformFacts | None:
    """The facts for a platform (case-insensitive), or ``None`` if it is unknown."""
    capabilities = get_platform_capabilities(platform)
    return None if capabilities is None else _facts(capabilities)


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


__all__ = [
    "DeliveryCardinality",
    "PlatformFacts",
    "ProgressStyle",
    "platform_delivers_one_reply",
    "platform_facts",
    "platform_is_known",
    "platform_supports_chat_delivery",
    "voice_note_format",
]
