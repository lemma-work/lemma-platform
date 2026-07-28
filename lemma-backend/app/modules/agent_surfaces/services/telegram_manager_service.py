from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    SurfaceConfig,
)
from app.modules.agent_surfaces.domain.errors import (
    TelegramManagedBotSetupNotFoundError,
    TelegramManagerNotConfiguredError,
)
from app.modules.agent_surfaces.platforms.telegram.client import (
    TelegramApiError,
    TelegramClient,
)
from app.modules.agent_surfaces.services.managed_bot_configurator import (
    configure_managed_bot,
)
from app.modules.agent_surfaces.services.managed_bot_persistence import (
    persist_managed_bot,
)
from app.modules.connectors.domain.errors import ConnectorNotFoundError

logger = get_logger(__name__)

TELEGRAM_MANAGER_ALLOWED_UPDATES = ["message", "managed_bot"]
_SETUP_TTL_SECONDS = 30 * 60
_START_RE = re.compile(r"^/start(?:@\w+)?\s+surface_([A-Za-z0-9_-]+)$")


class TelegramManagedBotSetupStatus(StrEnum):
    PENDING = "PENDING"
    WAITING_FOR_TELEGRAM = "WAITING_FOR_TELEGRAM"
    PROVISIONING = "PROVISIONING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class TelegramManagedBotSetup(BaseModel):
    setup_id: str
    request_id: int
    user_id: UUID
    organization_id: UUID
    pod_id: UUID
    surface_name: str
    pod_name: str
    agent_id: UUID | None = None
    surface_config: dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool = True
    suggested_bot_name: str
    suggested_bot_username: str
    status: TelegramManagedBotSetupStatus = TelegramManagedBotSetupStatus.PENDING
    telegram_user_id: int | None = None
    telegram_username: str | None = None
    telegram_display_name: str | None = None
    account_id: UUID | None = None
    surface_id: UUID | None = None
    bot_id: int | None = None
    bot_username: str | None = None
    error: str | None = None
    expires_at: datetime


class TelegramManagedBotSetupStore:
    def __init__(
        self,
        *,
        redis_url: str | None = None,
        ttl_seconds: int = _SETUP_TTL_SECONDS,
    ) -> None:
        self._redis_url = redis_url or settings.redis_url
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _setup_key(setup_id: str) -> str:
        return f"agent_surfaces:telegram_manager:setup:{setup_id}"

    @staticmethod
    def _request_key(request_id: int) -> str:
        return f"agent_surfaces:telegram_manager:request:{request_id}"

    @staticmethod
    def _telegram_user_key(telegram_user_id: int) -> str:
        return f"agent_surfaces:telegram_manager:user:{telegram_user_id}"

    async def create(self, setup: TelegramManagedBotSetup) -> bool:
        redis = Redis.from_url(self._redis_url, decode_responses=True)
        try:
            async with redis.pipeline(transaction=True) as pipe:
                pipe.set(
                    self._setup_key(setup.setup_id),
                    setup.model_dump_json(),
                    nx=True,
                    ex=self._ttl_seconds,
                )
                pipe.set(
                    self._request_key(setup.request_id),
                    setup.setup_id,
                    nx=True,
                    ex=self._ttl_seconds,
                )
                setup_created, request_created = await pipe.execute()
            if setup_created and request_created:
                return True
            if setup_created:
                await redis.delete(self._setup_key(setup.setup_id))
            if request_created:
                await redis.delete(self._request_key(setup.request_id))
            return False
        finally:
            await redis.aclose()

    async def get(self, setup_id: str) -> TelegramManagedBotSetup | None:
        redis = Redis.from_url(self._redis_url, decode_responses=True)
        try:
            raw = await redis.get(self._setup_key(setup_id))
            return TelegramManagedBotSetup.model_validate_json(raw) if raw else None
        finally:
            await redis.aclose()

    async def get_by_request_id(
        self, request_id: int
    ) -> TelegramManagedBotSetup | None:
        redis = Redis.from_url(self._redis_url, decode_responses=True)
        try:
            setup_id = await redis.get(self._request_key(request_id))
        finally:
            await redis.aclose()
        return await self.get(setup_id) if setup_id else None

    async def get_by_telegram_user_id(
        self, telegram_user_id: int
    ) -> TelegramManagedBotSetup | None:
        redis = Redis.from_url(self._redis_url, decode_responses=True)
        try:
            setup_id = await redis.get(self._telegram_user_key(telegram_user_id))
        finally:
            await redis.aclose()
        return await self.get(setup_id) if setup_id else None

    async def save(self, setup: TelegramManagedBotSetup) -> None:
        redis = Redis.from_url(self._redis_url, decode_responses=True)
        try:
            setup_key = self._setup_key(setup.setup_id)
            ttl = await redis.ttl(setup_key)
            if ttl <= 0:
                ttl = self._ttl_seconds
            await redis.set(setup_key, setup.model_dump_json(), ex=ttl)
            await redis.set(
                self._request_key(setup.request_id),
                setup.setup_id,
                ex=ttl,
            )
            if setup.telegram_user_id is not None:
                await redis.set(
                    self._telegram_user_key(setup.telegram_user_id),
                    setup.setup_id,
                    ex=ttl,
                )
        finally:
            await redis.aclose()

    async def load_offset(self) -> int | None:
        redis = Redis.from_url(self._redis_url, decode_responses=True)
        try:
            raw = await redis.get("agent_surfaces:telegram_manager:offset")
            return int(raw) if raw else None
        finally:
            await redis.aclose()

    async def save_offset(self, offset: int) -> None:
        redis = Redis.from_url(self._redis_url, decode_responses=True)
        try:
            await redis.set("agent_surfaces:telegram_manager:offset", str(offset))
        finally:
            await redis.aclose()


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
                    pod_name, surface_name, setup_id
                ),
                expires_at=datetime.now(timezone.utc)
                + timedelta(seconds=_SETUP_TTL_SECONDS),
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
        message = update.get("message")
        if not isinstance(message, dict):
            return

        text = str(message.get("text") or "").strip()
        start_match = _START_RE.match(text)
        if start_match:
            await self._handle_start(message, start_match.group(1))
            return

        created = message.get("managed_bot_created")
        if isinstance(created, dict):
            await self._handle_created(message, created)

    async def _handle_start(self, message: dict[str, Any], setup_id: str) -> None:
        setup = await self._store.get(setup_id)
        chat_id = _message_chat_id(message)
        telegram_user_id = _message_user_id(message)
        if setup is None:
            if chat_id is not None:
                await self._send_text(
                    chat_id,
                    "This setup link expired. Return to Lemma and start again.",
                )
            return
        if chat_id is None or telegram_user_id is None:
            return

        if (
            setup.telegram_user_id is not None
            and setup.telegram_user_id != telegram_user_id
        ):
            await self._send_text(
                chat_id,
                "This setup link is already being used in another Telegram account.",
            )
            return

        setup.telegram_user_id = telegram_user_id
        from_user = message.get("from") or {}
        setup.telegram_username = (
            str(from_user.get("username") or "").strip().lstrip("@") or None
        )
        setup.telegram_display_name = " ".join(
            part
            for part in (
                str(from_user.get("first_name") or "").strip(),
                str(from_user.get("last_name") or "").strip(),
            )
            if part
        ) or setup.telegram_username
        setup.status = TelegramManagedBotSetupStatus.WAITING_FOR_TELEGRAM
        await self._store.save(setup)
        await self._client.call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": (
                    "Create a dedicated Telegram bot for this Lemma surface. "
                    "You can edit the suggested name and username before confirming."
                ),
                "reply_markup": {
                    "keyboard": [
                        [
                            {
                                "text": "Create Telegram bot",
                                "request_managed_bot": {
                                    "request_id": setup.request_id,
                                    "suggested_name": setup.suggested_bot_name,
                                    # Telegram clients append the required "bot"
                                    # suffix in the managed-bot creation form.
                                    "suggested_username": (
                                        setup.suggested_bot_username[:-3]
                                    ),
                                },
                            }
                        ]
                    ],
                    "resize_keyboard": True,
                    "one_time_keyboard": True,
                },
            },
        )

    async def _handle_created(
        self,
        message: dict[str, Any],
        created: dict[str, Any],
    ) -> None:
        request_id = created.get("request_id")
        bot = created.get("bot")
        if not isinstance(bot, dict):
            return

        telegram_user_id = _message_user_id(message)
        chat_id = _message_chat_id(message)
        setup = (
            await self._store.get_by_request_id(request_id)
            if isinstance(request_id, int)
            else None
        )
        if setup is None and telegram_user_id is not None:
            # ManagedBotCreated contains only the bot in the current Bot API.
            # Correlate it with the setup most recently bound to this Telegram
            # user when they opened Lemma's /start link.
            setup = await self._store.get_by_telegram_user_id(telegram_user_id)
        if setup is None:
            return
        if (
            setup.telegram_user_id is not None
            and telegram_user_id != setup.telegram_user_id
        ):
            return
        if setup.status is TelegramManagedBotSetupStatus.COMPLETE:
            return

        setup.status = TelegramManagedBotSetupStatus.PROVISIONING
        setup.error = None
        await self._store.save(setup)

        try:
            bot_id = int(bot["id"])
            bot_username = str(bot.get("username") or "").strip().lstrip("@") or None
            token_response = await self._client.call(
                "getManagedBotToken", {"user_id": bot_id}
            )
            bot_token = str(token_response.get("result") or "").strip()
            if not bot_token:
                raise RuntimeError("Telegram did not return the managed bot token")

            setup.bot_id = bot_id
            setup.bot_username = bot_username
            account_id, surface_id = await self._persist_managed_bot(
                setup=setup,
                bot_id=bot_id,
                bot_username=bot_username,
                bot_token=bot_token,
            )
            await self._configure_managed_bot(
                setup=setup,
                bot_token=bot_token,
            )
            setup.status = TelegramManagedBotSetupStatus.COMPLETE
            setup.account_id = account_id
            setup.surface_id = surface_id
            setup.error = None
            await self._store.save(setup)
            if chat_id is not None:
                handle = f"@{bot_username}" if bot_username else "your new bot"
                await self._send_text(
                    chat_id,
                    f"{handle} is connected to Lemma and ready to use.",
                    remove_keyboard=True,
                )
                await self._client.call(
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": "Continue the conversation in your new bot.",
                        "reply_markup": {
                            "inline_keyboard": [
                                [
                                    {
                                        "text": "Open your bot",
                                        "url": self.bot_launch_url(setup),
                                    }
                                ]
                            ]
                        },
                    },
                )
        except (
            DomainError,
            KeyError,
            RedisError,
            RuntimeError,
            SQLAlchemyError,
            TelegramApiError,
            TypeError,
            ValueError,
        ) as exc:
            logger.error(
                "agent_surfaces.telegram_manager.managed_bot_provisioning_failed",
                exc_info=True,
            )
            setup.status = TelegramManagedBotSetupStatus.FAILED
            setup.error = _safe_setup_error(exc)
            await self._store.save(setup)
            if chat_id is not None:
                await self._send_text(
                    chat_id,
                    "The bot was created, but Lemma could not finish connecting it. "
                    "Return to Lemma and try again.",
                    remove_keyboard=True,
                )

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


def _message_chat_id(message: dict[str, Any]) -> int | None:
    value = (message.get("chat") or {}).get("id")
    return int(value) if isinstance(value, int) else None


def _message_user_id(message: dict[str, Any]) -> int | None:
    value = (message.get("from") or {}).get("id")
    return int(value) if isinstance(value, int) else None


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


def _safe_setup_error(exc: Exception) -> str:
    if isinstance(exc, ConnectorNotFoundError):
        return "Telegram connector is not installed for this organization."
    return "Lemma could not finish connecting the managed bot."
