from __future__ import annotations

import asyncio
import re
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from redis.exceptions import RedisError

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.request_context import create_background_task
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import SurfaceConfig
from app.modules.agent_surfaces.domain.errors import (
    TelegramManagedBotSetupNotFoundError,
    TelegramManagerNotConfiguredError,
)
from app.modules.agent_surfaces.platforms.telegram.client import TelegramClient
from app.modules.agent_surfaces.services.managed_bot_configurator import (
    configure_managed_bot,
)
from app.modules.agent_surfaces.services.managed_bot_persistence import (
    persist_managed_bot,
)
from app.modules.agent_surfaces.services.telegram_manager_store import (
    PROVISIONING_LEASE_REFRESH_SECONDS,
    TELEGRAM_MANAGED_BOT_SETUP_TTL_SECONDS,
    TelegramManagedBotProvisioningClaim,
    TelegramManagedBotProvisioningInProgressError,
    TelegramManagedBotSetup,
    TelegramManagedBotSetupStatus,
    TelegramManagedBotSetupStore,
)
from app.modules.agent_surfaces.services.telegram_manager_updates import (
    handle_telegram_manager_update,
)

TELEGRAM_MANAGER_ALLOWED_UPDATES = ["message", "managed_bot"]

__all__ = [
    "TELEGRAM_MANAGER_ALLOWED_UPDATES",
    "TelegramManagedBotProvisioningClaim",
    "TelegramManagedBotProvisioningInProgressError",
    "TelegramManagedBotSetup",
    "TelegramManagedBotSetupStatus",
    "TelegramManagedBotSetupStore",
    "TelegramManagerService",
]


class TelegramManagerService:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        store: TelegramManagedBotSetupStore | None = None,
        manager_token: str | None = None,
        manager_username: str | None = None,
        api_base_url: str | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._store = store or TelegramManagedBotSetupStore()
        self._manager_token = (
            manager_token
            if manager_token is not None
            else surface_settings.telegram_manager_bot_token
        )
        raw_username = (
            manager_username
            if manager_username is not None
            else surface_settings.telegram_manager_bot_username
        )
        self._manager_username = str(raw_username or "").strip().lstrip("@")
        self._api_base_url = api_base_url
        credentials: dict[str, Any] = {"bot_token": self._manager_token or ""}
        if api_base_url:
            credentials["api_base_url"] = api_base_url
        self._client = TelegramClient.from_credentials(credentials, timeout=65)

    @property
    def configured(self) -> bool:
        return bool(self._manager_token and self._manager_username)

    @property
    def manager_username(self) -> str:
        return self._manager_username

    async def start_setup(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        pod_id: UUID,
        surface_name: str,
        agent_id: UUID | None,
        surface_config: SurfaceConfig,
        is_enabled: bool,
        pod_name: str,
    ) -> TelegramManagedBotSetup:
        if not self.configured:
            raise TelegramManagerNotConfiguredError()

        for _ in range(5):
            setup_id = secrets.token_urlsafe(18)
            request_id = secrets.randbelow(2_147_483_646) + 1
            setup = TelegramManagedBotSetup(
                setup_id=setup_id,
                request_id=request_id,
                user_id=user_id,
                organization_id=organization_id,
                pod_id=pod_id,
                surface_name=surface_name,
                pod_name=pod_name,
                agent_id=agent_id,
                surface_config=surface_config.model_dump(mode="json"),
                is_enabled=is_enabled,
                suggested_bot_name=_suggested_bot_name(pod_name, surface_name),
                suggested_bot_username=_suggested_bot_username(
                    pod_name,
                    surface_name,
                    setup_id,
                ),
                expires_at=datetime.now(timezone.utc)
                + timedelta(seconds=TELEGRAM_MANAGED_BOT_SETUP_TTL_SECONDS),
            )
            if await self._store.create(setup):
                return setup
        raise RuntimeError("Could not allocate a Telegram managed-bot setup")

    def launch_url(self, setup: TelegramManagedBotSetup) -> str:
        if not self.configured:
            raise TelegramManagerNotConfiguredError()
        return (
            f"https://t.me/{self._manager_username}"
            f"?start=surface_{setup.setup_id}"
        )

    async def get_setup(
        self,
        *,
        setup_id: str,
        user_id: UUID,
        pod_id: UUID,
    ) -> TelegramManagedBotSetup:
        setup = await self._store.get(setup_id)
        if setup is None or setup.user_id != user_id or setup.pod_id != pod_id:
            raise TelegramManagedBotSetupNotFoundError(setup_id)
        return setup

    async def handle_update(self, update: dict[str, Any]) -> None:
        await handle_telegram_manager_update(self, update)

    async def _persist_managed_bot(
        self,
        *,
        setup: TelegramManagedBotSetup,
        bot_id: int,
        bot_username: str | None,
        bot_token: str,
    ) -> tuple[UUID, UUID]:
        return await persist_managed_bot(
            uow_factory=self._uow_factory,
            setup=setup,
            bot_id=bot_id,
            bot_username=bot_username,
            bot_token=bot_token,
        )

    def bot_launch_url(self, setup: TelegramManagedBotSetup) -> str:
        if not setup.bot_username:
            return self.launch_url(setup)
        return f"https://t.me/{setup.bot_username}?start=lemma"

    async def _configure_managed_bot(
        self,
        *,
        setup: TelegramManagedBotSetup,
        bot_token: str,
    ) -> None:
        await configure_managed_bot(
            uow_factory=self._uow_factory,
            api_base_url=self._api_base_url,
            bot_token=bot_token,
            pod_id=setup.pod_id,
            surface_name=setup.surface_name,
            pod_name=setup.pod_name,
            surface_config=setup.surface_config,
        )

    @asynccontextmanager
    async def _renew_provisioning_lease(
        self,
        *,
        setup_id: str,
        owner: str,
    ) -> AsyncIterator[None]:
        owner_task = asyncio.current_task()
        lease_lost_message = (
            f"Telegram provisioning lease lost for {setup_id}:{owner}"
        )

        async def _renew() -> None:
            while True:
                await asyncio.sleep(PROVISIONING_LEASE_REFRESH_SECONDS)
                try:
                    refreshed = await self._store.refresh_provisioning_lease(
                        setup_id=setup_id,
                        owner=owner,
                    )
                except RedisError:
                    refreshed = False
                if not refreshed:
                    if owner_task is not None:
                        owner_task.cancel(lease_lost_message)
                    return

        renew_task = create_background_task(
            _renew(),
            name=f"telegram-managed-bot-lease-{setup_id}",
        )
        try:
            try:
                yield
            except asyncio.CancelledError as exc:
                if exc.args != (lease_lost_message,):
                    raise
                if owner_task is not None:
                    owner_task.uncancel()
                raise RuntimeError(lease_lost_message) from exc
        finally:
            renew_task.cancel()
            await asyncio.gather(renew_task, return_exceptions=True)
            await self._store.release_provisioning_lease(
                setup_id=setup_id,
                owner=owner,
            )

    async def _send_text(
        self,
        chat_id: int,
        text: str,
        *,
        remove_keyboard: bool = False,
    ) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if remove_keyboard:
            payload["reply_markup"] = {"remove_keyboard": True}
        await self._client.call("sendMessage", payload)


def _suggested_bot_name(pod_name: str, surface_name: str) -> str:
    name = f"{pod_name.strip()} · {surface_name.strip()}".strip(" ·")
    return (name or "Lemma agent")[:64]


def _suggested_bot_username(
    pod_name: str,
    surface_name: str,
    setup_id: str,
) -> str:
    base = re.sub(
        r"[^A-Za-z0-9_]+",
        "_",
        f"lemma_{pod_name}_{surface_name}",
    ).strip("_").lower()
    suffix = setup_id[:4].lower()
    max_base = 32 - len(f"_{suffix}_bot")
    base = base[:max_base].rstrip("_") or "lemma"
    username = f"{base}_{suffix}_bot"
    if len(username) < 5:
        username = f"lemma_{suffix}_bot"
    return username
