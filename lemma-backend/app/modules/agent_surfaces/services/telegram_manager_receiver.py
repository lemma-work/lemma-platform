from __future__ import annotations

import asyncio
from typing import Any

import httpx
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.db.session import async_session_maker
from app.core.log.log import get_logger
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.platforms.common import (
    public_https_api_url_available,
)
from app.modules.agent_surfaces.platforms.telegram.client import (
    TelegramApiError,
    TelegramClient,
    classify_telegram_error,
    telegram_retry_after,
)
from app.modules.agent_surfaces.platforms.delivery import DeliveryClassification
from app.modules.agent_surfaces.services.telegram_manager_service import (
    TELEGRAM_MANAGER_ALLOWED_UPDATES,
    TelegramManagerService,
)
from app.modules.agent_surfaces.services.telegram_manager_store import (
    TelegramManagedBotSetupStore,
)

logger = get_logger(__name__)

_WEBHOOK_RETRY_INITIAL_SECONDS = 5.0
_WEBHOOK_RETRY_MAX_SECONDS = 300.0


class TelegramManagerPollingReceiver:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory | None = None,
        store: TelegramManagedBotSetupStore | None = None,
        manager_token: str | None = None,
        manager_username: str | None = None,
        api_base_url: str | None = None,
    ) -> None:
        self._uow_factory = uow_factory or SessionUnitOfWorkFactory(
            async_session_maker
        )
        self._store = store or TelegramManagedBotSetupStore()
        self._manager_token = (
            manager_token
            if manager_token is not None
            else surface_settings.telegram_manager_bot_token
        )
        credentials: dict[str, Any] = {"bot_token": self._manager_token or ""}
        if api_base_url:
            credentials["api_base_url"] = api_base_url
        self._client = TelegramClient.from_credentials(credentials, timeout=65)
        self._service = TelegramManagerService(
            uow_factory=self._uow_factory,
            store=self._store,
            manager_token=self._manager_token,
            manager_username=manager_username,
            api_base_url=api_base_url,
        )

    def should_start(self) -> bool:
        return bool(
            surface_settings.enable_telegram_manager_polling_mode
            and self._manager_token
            and self._service.configured
        )

    async def run(self) -> None:
        if not self.should_start():
            return
        webhook_deleted = False
        offset_loaded = False
        offset: int | None = None
        while True:
            try:
                if not webhook_deleted:
                    await self._client.call(
                        "deleteWebhook",
                        {"drop_pending_updates": False},
                    )
                    webhook_deleted = True
                if not offset_loaded:
                    offset = await self._store.load_offset()
                    offset_loaded = True
                payload: dict[str, Any] = {
                    "timeout": 30,
                    "allowed_updates": TELEGRAM_MANAGER_ALLOWED_UPDATES,
                }
                if offset is not None:
                    payload["offset"] = offset
                response = await self._client.call("getUpdates", payload)
                for update in response.get("result") or []:
                    if not isinstance(update, dict):
                        continue
                    await self._service.handle_update(update)
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        next_offset = update_id + 1
                        await self._store.save_offset(next_offset)
                        offset = next_offset
            except asyncio.CancelledError:
                raise
            except (
                DomainError,
                httpx.RequestError,
                RedisError,
                RuntimeError,
                SQLAlchemyError,
                TelegramApiError,
                TypeError,
                ValueError,
            ):
                logger.error(
                    "agent_surfaces.telegram_manager.polling_receiver_failed",
                    exc_info=True,
                )
                await asyncio.sleep(5)


async def register_telegram_manager_webhook() -> None:
    if (
        not surface_settings.telegram_manager_bot_token
        or not surface_settings.telegram_manager_bot_username
        or surface_settings.enable_telegram_manager_polling_mode
        or not public_https_api_url_available()
    ):
        return
    secret = str(surface_settings.telegram_manager_webhook_secret or "").strip()
    if not secret:
        logger.warning(
            "agent_surfaces.telegram_manager.webhook_secret_missing"
        )
        return
    client = TelegramClient(
        bot_token=surface_settings.telegram_manager_bot_token,
        timeout=20,
    )
    webhook_url = (
        f"{settings.api_url.rstrip('/')}/surfaces/webhooks/telegram-manager"
    )
    await client.call(
        "setWebhook",
        {
            "url": webhook_url,
            "secret_token": secret,
            "allowed_updates": TELEGRAM_MANAGER_ALLOWED_UPDATES,
            "drop_pending_updates": False,
        },
    )


async def run_telegram_manager_webhook_registration() -> None:
    delay = _WEBHOOK_RETRY_INITIAL_SECONDS
    while True:
        try:
            await register_telegram_manager_webhook()
            return
        except asyncio.CancelledError:
            raise
        except TelegramApiError as exc:
            logger.error(
                "agent_surfaces.telegram_manager.webhook_registration_failed",
                exc_info=True,
            )
            if (
                classify_telegram_error(exc)
                is DeliveryClassification.PERMANENT
            ):
                return
            retry_after = telegram_retry_after(exc)
        except httpx.RequestError:
            logger.error(
                "agent_surfaces.telegram_manager.webhook_registration_failed",
                exc_info=True,
            )
            retry_after = None
        await asyncio.sleep(
            min(
                _WEBHOOK_RETRY_MAX_SECONDS,
                max(delay, retry_after or 0),
            )
        )
        delay = min(delay * 2, _WEBHOOK_RETRY_MAX_SECONDS)
