from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from app.modules.agent_surfaces.platforms.telegram.client import TelegramClient
from app.modules.agent_surfaces.services.telegram_manager_store import (
    TelegramManagedBotSetup,
    TelegramManagedBotSetupStore,
)


class TelegramManagerRuntime(Protocol):
    _store: TelegramManagedBotSetupStore
    _client: TelegramClient

    async def _send_text(
        self,
        chat_id: int,
        text: str,
        *,
        remove_keyboard: bool = False,
    ) -> None:
        raise NotImplementedError

    async def _persist_managed_bot(
        self,
        *,
        setup: TelegramManagedBotSetup,
        bot_id: int,
        bot_username: str | None,
        bot_token: str,
    ) -> tuple[UUID, UUID]:
        raise NotImplementedError

    async def _configure_managed_bot(
        self,
        *,
        setup: TelegramManagedBotSetup,
        bot_token: str,
    ) -> None:
        raise NotImplementedError

    def _renew_provisioning_lease(
        self,
        *,
        setup_id: str,
        owner: str,
    ) -> AbstractAsyncContextManager[None]:
        raise NotImplementedError

    def bot_launch_url(self, setup: TelegramManagedBotSetup) -> str:
        raise NotImplementedError
