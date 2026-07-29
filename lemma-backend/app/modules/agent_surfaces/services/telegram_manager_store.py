from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.core.config import settings
from app.modules.agent_surfaces.domain.errors import (
    TelegramManagedBotSetupAlreadyInProgressError,
)

TELEGRAM_MANAGED_BOT_SETUP_TTL_SECONDS = 30 * 60
PROVISIONING_LEASE_SECONDS = 60
PROVISIONING_LEASE_REFRESH_SECONDS = 20

_COMPARE_DELETE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""

_BIND_TELEGRAM_USER_SCRIPT = """
local current = redis.call('get', KEYS[1])
if current and current ~= ARGV[1] then
  return 0
end
local setup_ttl = redis.call('ttl', KEYS[2])
if setup_ttl <= 0 then
  return 0
end
redis.call('set', KEYS[1], ARGV[1], 'EX', setup_ttl)
return 1
"""

_SAVE_IF_STATUS_SCRIPT = """
local raw = redis.call('get', KEYS[1])
if not raw then
  return 0
end
local current = cjson.decode(raw)
local expected = cjson.decode(ARGV[1])
local matches = false
for _, status in ipairs(expected) do
  if current['status'] == status then
    matches = true
    break
  end
end
if not matches then
  return 0
end
local updated = cjson.decode(ARGV[2])
local next_status = updated['status']
local valid_transition = (
  current['status'] == 'PENDING'
  and (next_status == 'WAITING_FOR_TELEGRAM' or next_status == 'FAILED')
) or (
  current['status'] == 'WAITING_FOR_TELEGRAM'
  and (
    next_status == 'WAITING_FOR_TELEGRAM'
    or next_status == 'PROVISIONING'
    or next_status == 'FAILED'
  )
) or (
  current['status'] == 'PROVISIONING'
  and (next_status == 'COMPLETE' or next_status == 'FAILED')
)
if not valid_transition then
  return 0
end
local requires_owner = (
  current['status'] == 'PROVISIONING' or next_status == 'PROVISIONING'
)
if requires_owner and (
  ARGV[3] == '' or redis.call('get', KEYS[2]) ~= ARGV[3]
) then
  return 0
end
redis.call('set', KEYS[1], ARGV[2], 'KEEPTTL')
return 1
"""

_CLAIM_PROVISIONING_SCRIPT = """
local bound_bot = redis.call('get', KEYS[1])
if bound_bot and bound_bot ~= ARGV[1] then
  return -1
end
if not bound_bot then
  redis.call('set', KEYS[1], ARGV[1], 'EX', ARGV[3])
end
local acquired = redis.call(
  'set', KEYS[2], ARGV[2], 'NX', 'EX', ARGV[4]
)
if acquired then
  return 1
end
return 0
"""

_REFRESH_LEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""


async def _eval(
    redis: Redis,
    script: str,
    numkeys: int,
    *keys_and_args: str,
) -> Any:
    return await redis.eval(  # type: ignore[reportGeneralTypeIssues,reportArgumentType]
        script,
        numkeys,
        *keys_and_args,
    )


class TelegramManagedBotSetupStatus(StrEnum):
    PENDING = "PENDING"
    WAITING_FOR_TELEGRAM = "WAITING_FOR_TELEGRAM"
    PROVISIONING = "PROVISIONING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class TelegramManagedBotProvisioningClaim(StrEnum):
    ACQUIRED = "ACQUIRED"
    IN_PROGRESS = "IN_PROGRESS"
    BOT_CONFLICT = "BOT_CONFLICT"


class TelegramManagedBotProvisioningInProgressError(RuntimeError):
    pass


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
        ttl_seconds: int = TELEGRAM_MANAGED_BOT_SETUP_TTL_SECONDS,
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

    @staticmethod
    def _target_key(pod_id: UUID, surface_name: str) -> str:
        return (
            "agent_surfaces:telegram_manager:target:"
            f"{pod_id}:{surface_name.strip().casefold()}"
        )

    @staticmethod
    def _bot_key(setup_id: str) -> str:
        return f"agent_surfaces:telegram_manager:bot:{setup_id}"

    @staticmethod
    def _provisioning_key(setup_id: str) -> str:
        return f"agent_surfaces:telegram_manager:provisioning:{setup_id}"

    @staticmethod
    def _processed_update_key(update_id: int) -> str:
        return f"agent_surfaces:telegram_manager:update:{update_id}"

    async def create(self, setup: TelegramManagedBotSetup) -> bool:
        redis = Redis.from_url(self._redis_url, decode_responses=True)
        target_key = self._target_key(setup.pod_id, setup.surface_name)
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
                pipe.set(
                    target_key,
                    setup.setup_id,
                    nx=True,
                    ex=self._ttl_seconds,
                )
                setup_created, request_created, target_created = await pipe.execute()
            if setup_created and request_created and target_created:
                return True
            if setup_created:
                await redis.delete(self._setup_key(setup.setup_id))
            if request_created:
                await redis.delete(self._request_key(setup.request_id))
            if target_created:
                await _eval(
                    redis,
                    _COMPARE_DELETE_SCRIPT,
                    1,
                    target_key,
                    setup.setup_id,
                )
            if not target_created:
                raise TelegramManagedBotSetupAlreadyInProgressError(
                    setup.surface_name
                )
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

    async def bind_telegram_user(
        self,
        *,
        setup_id: str,
        telegram_user_id: int,
    ) -> bool:
        redis = Redis.from_url(self._redis_url, decode_responses=True)
        try:
            result = await _eval(
                redis,
                _BIND_TELEGRAM_USER_SCRIPT,
                2,
                self._telegram_user_key(telegram_user_id),
                self._setup_key(setup_id),
                setup_id,
            )
            return bool(result)
        finally:
            await redis.aclose()

    async def save_if_status(
        self,
        setup: TelegramManagedBotSetup,
        *,
        expected: set[TelegramManagedBotSetupStatus],
        owner: str | None = None,
    ) -> bool:
        redis = Redis.from_url(self._redis_url, decode_responses=True)
        try:
            result = await _eval(
                redis,
                _SAVE_IF_STATUS_SCRIPT,
                2,
                self._setup_key(setup.setup_id),
                self._provisioning_key(setup.setup_id),
                json.dumps(sorted(status.value for status in expected)),
                setup.model_dump_json(),
                owner or "",
            )
            return bool(result)
        finally:
            await redis.aclose()

    async def claim_provisioning(
        self,
        *,
        setup_id: str,
        bot_id: int,
        owner: str,
    ) -> TelegramManagedBotProvisioningClaim:
        redis = Redis.from_url(self._redis_url, decode_responses=True)
        try:
            result = int(
                await _eval(
                    redis,
                    _CLAIM_PROVISIONING_SCRIPT,
                    2,
                    self._bot_key(setup_id),
                    self._provisioning_key(setup_id),
                    str(bot_id),
                    owner,
                    str(self._ttl_seconds),
                    str(PROVISIONING_LEASE_SECONDS),
                )
            )
        finally:
            await redis.aclose()
        if result == 1:
            return TelegramManagedBotProvisioningClaim.ACQUIRED
        if result == -1:
            return TelegramManagedBotProvisioningClaim.BOT_CONFLICT
        return TelegramManagedBotProvisioningClaim.IN_PROGRESS

    async def refresh_provisioning_lease(
        self,
        *,
        setup_id: str,
        owner: str,
    ) -> bool:
        redis = Redis.from_url(self._redis_url, decode_responses=True)
        try:
            result = await _eval(
                redis,
                _REFRESH_LEASE_SCRIPT,
                1,
                self._provisioning_key(setup_id),
                owner,
                str(PROVISIONING_LEASE_SECONDS),
            )
            return bool(result)
        finally:
            await redis.aclose()

    async def release_provisioning_lease(
        self,
        *,
        setup_id: str,
        owner: str,
    ) -> None:
        redis = Redis.from_url(self._redis_url, decode_responses=True)
        try:
            await _eval(
                redis,
                _COMPARE_DELETE_SCRIPT,
                1,
                self._provisioning_key(setup_id),
                owner,
            )
        finally:
            await redis.aclose()

    async def release_reservations(self, setup: TelegramManagedBotSetup) -> None:
        redis = Redis.from_url(self._redis_url, decode_responses=True)
        try:
            async with redis.pipeline(transaction=True) as pipe:
                pipe.eval(
                    _COMPARE_DELETE_SCRIPT,
                    1,
                    self._target_key(setup.pod_id, setup.surface_name),
                    setup.setup_id,
                )
                if setup.telegram_user_id is not None:
                    pipe.eval(
                        _COMPARE_DELETE_SCRIPT,
                        1,
                        self._telegram_user_key(setup.telegram_user_id),
                        setup.setup_id,
                    )
                await pipe.execute()
        finally:
            await redis.aclose()

    async def is_update_processed(self, update_id: int) -> bool:
        redis = Redis.from_url(self._redis_url, decode_responses=True)
        try:
            return bool(await redis.exists(self._processed_update_key(update_id)))
        finally:
            await redis.aclose()

    async def mark_update_processed(self, update_id: int) -> None:
        redis = Redis.from_url(self._redis_url, decode_responses=True)
        try:
            await redis.set(
                self._processed_update_key(update_id),
                "1",
                ex=self._ttl_seconds,
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
