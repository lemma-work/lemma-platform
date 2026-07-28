from __future__ import annotations

from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.services.telegram_mini_app_service import (
    sync_telegram_mini_app,
)


class TelegramMiniAppSyncMixin:
    async def sync_telegram_mini_app(
        self,
        surface: AgentSurfaceEntity,
    ) -> None:
        await sync_telegram_mini_app(
            surface=surface,
            credential_resolver=self._credential_resolver,
            uow=self.surface_repository.uow,
        )
