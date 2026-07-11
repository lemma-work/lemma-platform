"""Surface-platform capability: a standing system-prompt fragment that makes the
agent aware it is conversing on a third-party platform (Slack/Teams/Telegram/
WhatsApp/email) and how its messages, files, and forms are delivered there.

This is a pure-instructions capability (no toolset) — the surface *tools* are
contributed separately via ``SurfacePlatformToolFactory``. Mirrors
``CurrentTimeCapability`` in carrying no ``get_toolset``.
"""

from __future__ import annotations

from pydantic_ai.capabilities import AbstractCapability

from app.composition.agent_surface_runtime import platform_agent_guidance


class SurfacePlatformCapability(AbstractCapability[object]):
    """Append per-platform guidance to the cached system-prompt prefix."""

    def __init__(self, platform: str) -> None:
        self._platform = platform

    def get_serialization_name(self) -> str | None:  # pragma: no cover - metadata
        return "surface_platform"

    def get_instructions(self) -> str:
        return platform_agent_guidance(self._platform)
