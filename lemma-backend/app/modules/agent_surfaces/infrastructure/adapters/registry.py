from __future__ import annotations

from app.modules.agent_surfaces.domain.ports import SurfacePlatformAdapterPort
from app.modules.agent_surfaces.platforms.gmail.adapter import GmailSurfaceAdapter
from app.modules.agent_surfaces.platforms.outlook.adapter import OutlookSurfaceAdapter
from app.modules.agent_surfaces.platforms.resend.adapter import ResendSurfaceAdapter
from app.modules.agent_surfaces.platforms.slack.adapter import SlackSurfaceAdapter
from app.modules.agent_surfaces.platforms.teams.adapter import TeamsSurfaceAdapter
from app.modules.agent_surfaces.platforms.telegram.adapter import TelegramSurfaceAdapter
from app.modules.agent_surfaces.platforms.whatsapp.adapter import WhatsAppSurfaceAdapter


class SurfacePlatformAdapterRegistry:
    def __init__(self, extra_adapters: dict[str, SurfacePlatformAdapterPort] | None = None):
        self._adapters: dict[str, SurfacePlatformAdapterPort] = {
            "SLACK": SlackSurfaceAdapter(),
            "TEAMS": TeamsSurfaceAdapter(),
            "WHATSAPP": WhatsAppSurfaceAdapter(),
            "TELEGRAM": TelegramSurfaceAdapter(),
            "GMAIL": GmailSurfaceAdapter(),
            "OUTLOOK": OutlookSurfaceAdapter(),
            "RESEND": ResendSurfaceAdapter(),
        }
        if extra_adapters:
            self._adapters.update(extra_adapters)

    def get(self, platform: str) -> SurfacePlatformAdapterPort | None:
        key = platform.value if hasattr(platform, "value") else str(platform)
        return self._adapters.get(str(key).upper())

    def register(self, platform: str, adapter: SurfacePlatformAdapterPort) -> None:
        key = platform.value if hasattr(platform, "value") else str(platform)
        self._adapters[str(key).upper()] = adapter

    def list_platforms(self) -> list[str]:
        return list(self._adapters.keys())
