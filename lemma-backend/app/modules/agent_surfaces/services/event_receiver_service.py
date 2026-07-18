from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import httpx
from redis.asyncio import Redis

from app.core.config import settings
from app.modules.agent_surfaces.config import surface_settings
from app.core.infrastructure.channels.channel_service import channel_service
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.events.inbox import stable_event_id
from app.core.infrastructure.events.publisher import EventPublisher
from app.core.log.log import get_logger
from app.core.request_context import create_background_task
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.events import SurfaceWebhookReceivedEvent
from app.modules.agent_surfaces.infrastructure.adapters.account_adapter import (
    SqlAlchemySurfaceAccountAdapter,
)
from app.modules.agent_surfaces.platforms.slack.client import slack_access_token
from app.modules.agent_surfaces.platforms.telegram.client import (
    normalize_bot_base_url,
    resolve_api_base,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)

logger = get_logger(__name__)

_TELEGRAM_CONFLICT_GRACE_SECONDS = 75
_RECEIVER_CHANGED_CHANNEL = "agent_surfaces.receiver.changed"
_LEASE_TTL_SECONDS = 30
_LEASE_REFRESH_SECONDS = 10
_DEFAULT_SCAN_INTERVAL_SECONDS = 15.0
_RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


class ReceiverRunner(Protocol):
    async def run(self) -> None: ...


ReceiverRunnerFactory = Callable[["NativeReceiverCandidate"], ReceiverRunner]


@dataclass(frozen=True)
class NativeReceiverCandidate:
    key: str
    platform: SurfacePlatform
    surface_ids: tuple[UUID, ...]
    credential_label: str
    credentials: dict[str, Any]


async def notify_surface_receiver_config_changed(
    surface_id: UUID | None = None,
) -> None:
    """Wake native receiver coordinators after surface create/update/delete."""
    try:
        await channel_service.publish(
            _RECEIVER_CHANGED_CHANNEL,
            {"surface_id": str(surface_id) if surface_id else None},
        )
    except Exception:
        logger.debug("agent_surfaces.event_receiver_service.could_not_publish_surface_receiver.observed", exc_info=True)


class SurfaceEventReceiverService:
    """Runs DB-backed native surface receivers on worker startup."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], Any] | None = None,
        scan_interval_seconds: float = _DEFAULT_SCAN_INTERVAL_SECONDS,
        redis_url: str | None = None,
        runner_factories: dict[SurfacePlatform, ReceiverRunnerFactory] | None = None,
    ) -> None:
        self._coordinator = NativeSurfaceReceiverCoordinator(
            uow_factory=uow_factory or SessionUnitOfWorkFactory(async_session_maker),
            scan_interval_seconds=scan_interval_seconds,
            redis_url=redis_url or settings.redis_url,
            runner_factories=runner_factories,
        )

    def should_start(self) -> bool:
        return bool(
            surface_settings.enable_telegram_polling_mode
            or surface_settings.enable_slack_socket_mode
        )

    async def run(self) -> None:
        if not self.should_start():
            return
        await self._coordinator.run()

    async def stop(self) -> None:
        await self._coordinator.stop()


class NativeSurfaceReceiverCoordinator:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], Any],
        scan_interval_seconds: float,
        redis_url: str,
        runner_factories: dict[SurfacePlatform, ReceiverRunnerFactory] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._scan_interval_seconds = scan_interval_seconds
        self._redis_url = redis_url
        self._redis: Redis | None = None
        self._owner = f"{socket.gethostname()}:{uuid.uuid4()}"
        self._tasks: dict[str, asyncio.Task] = {}
        self._wakeup = asyncio.Event()
        self._stopping = False
        self._listener_task: asyncio.Task | None = None
        self._runner_factories = runner_factories or {
            SurfacePlatform.TELEGRAM: TelegramPollingReceiverRunner,
            SurfacePlatform.SLACK: SlackSocketReceiverRunner,
        }

    async def run(self) -> None:
        self._redis = Redis.from_url(
            self._redis_url,
            decode_responses=True,
            health_check_interval=30,
            socket_keepalive=True,
            max_connections=settings.redis_max_connections,
        )
        self._listener_task = create_background_task(
            self._listen_for_wakeups(), name="surface-receiver-wakeups"
        )
        try:
            while not self._stopping:
                await self.reconcile()
                try:
                    await asyncio.wait_for(
                        self._wakeup.wait(),
                        timeout=self._scan_interval_seconds,
                    )
                except TimeoutError:
                    pass
                self._wakeup.clear()
        finally:
            await self._shutdown()

    async def stop(self) -> None:
        """Signal shutdown; the active run loop owns resource release."""
        self._stopping = True
        self._wakeup.set()

    async def _shutdown(self) -> None:
        if self._listener_task is not None:
            self._listener_task.cancel()
            await asyncio.gather(self._listener_task, return_exceptions=True)
            self._listener_task = None
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def reconcile(self) -> None:
        desired = {
            candidate.key: candidate for candidate in await self._load_candidates()
        }
        for key in list(self._tasks):
            task = self._tasks[key]
            if task.done() or key not in desired:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self._tasks.pop(key, None)
                await self._release_lease(key)

        for key, candidate in desired.items():
            if key in self._tasks and not self._tasks[key].done():
                continue
            if await self._acquire_lease(key):
                self._tasks[key] = create_background_task(
                    self._run_leased_receiver(candidate),
                    name=f"surface-receiver-{key}",
                )

    async def _load_candidates(self) -> list[NativeReceiverCandidate]:
        platforms: set[SurfacePlatform] = set()
        if surface_settings.enable_telegram_polling_mode:
            platforms.add(SurfacePlatform.TELEGRAM)
        if surface_settings.enable_slack_socket_mode:
            platforms.add(SurfacePlatform.SLACK)
        if not platforms:
            return []

        async with self._uow_factory() as uow:
            repository = SurfaceRepository(uow)
            account_port = SqlAlchemySurfaceAccountAdapter(uow)
            surfaces = await repository.list_active_native_receiver_surfaces(platforms)
            account_cache: dict[UUID, dict[str, Any]] = {}
            candidates: dict[str, NativeReceiverCandidate] = {}

            for surface in surfaces:
                credentials = await _receiver_credentials(
                    surface, account_port, account_cache
                )
                if credentials is None:
                    continue
                candidate = _candidate_from_surface(surface, credentials)
                if candidate is None:
                    continue
                existing = candidates.get(candidate.key)
                if existing is None:
                    candidates[candidate.key] = candidate
                else:
                    candidates[candidate.key] = NativeReceiverCandidate(
                        key=existing.key,
                        platform=existing.platform,
                        surface_ids=tuple(sorted({*existing.surface_ids, surface.id})),
                        credential_label=existing.credential_label,
                        credentials=existing.credentials,
                    )
            return list(candidates.values())

    async def _listen_for_wakeups(self) -> None:
        assert self._redis is not None
        pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe(_RECEIVER_CHANGED_CHANNEL)
        try:
            async for _ in pubsub.listen():
                self._wakeup.set()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug('agent_surfaces.event_receiver_service.native_surface_receiver_wakeup_listener.diagnostic', exc_info=True)
            self._wakeup.set()
        finally:
            await pubsub.unsubscribe(_RECEIVER_CHANGED_CHANNEL)
            await pubsub.aclose()

    async def _run_leased_receiver(self, candidate: NativeReceiverCandidate) -> None:
        runner = self._runner_factories[candidate.platform](candidate)
        runner_task = create_background_task(
            runner.run(), name=f"surface-runner-{candidate.key}"
        )
        heartbeat = create_background_task(
            self._refresh_lease_loop(candidate.key),
            name=f"surface-receiver-lease-{candidate.key}",
        )
        try:
            done, pending = await asyncio.wait(
                {runner_task, heartbeat},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                task.result()
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug('agent_surfaces.event_receiver_service.native_surface_receiver_stopped_platform.diagnostic', exc_info=True)
        finally:
            for task in (runner_task, heartbeat):
                task.cancel()
            await asyncio.gather(runner_task, heartbeat, return_exceptions=True)
            await self._release_lease(candidate.key)

    async def _acquire_lease(self, key: str) -> bool:
        assert self._redis is not None
        return bool(
            await self._redis.set(
                _lease_key(key),
                self._owner,
                nx=True,
                ex=_LEASE_TTL_SECONDS,
            )
        )

    async def _refresh_lease_loop(self, key: str) -> None:
        assert self._redis is not None
        while True:
            await asyncio.sleep(_LEASE_REFRESH_SECONDS)
            if await self._redis.get(_lease_key(key)) != self._owner:
                raise RuntimeError(f"Native receiver lease lost for {key}")
            await self._redis.expire(_lease_key(key), _LEASE_TTL_SECONDS)

    async def _release_lease(self, key: str) -> None:
        if self._redis is None:
            return
        await self._redis.eval(_RELEASE_LOCK_SCRIPT, 1, _lease_key(key), self._owner)


class TelegramPollingReceiverRunner:
    def __init__(self, candidate: NativeReceiverCandidate) -> None:
        self._candidate = candidate
        self._bot_token = str(candidate.credentials.get("bot_token") or "").strip()
        self._api_base = resolve_api_base(candidate.credentials)

    async def run(self) -> None:
        if not self._bot_token:
            logger.debug('agent_surfaces.event_receiver_service.telegram_native_receiver_missing_bot.diagnostic')
            return

        base_url = normalize_bot_base_url(self._api_base, self._bot_token)
        offset: int | None = None
        conflict_deadline: float | None = None

        # Long-poll timeout is 30s; give the HTTP read 60s so transient
        # network latency or a slow Telegram response doesn't surface as a
        # noisy ReadTimeout warning. Connect timeout stays short.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout=60.0, connect=10.0)
        ) as client:
            await self._telegram_api(
                client,
                base_url,
                "deleteWebhook",
                {"drop_pending_updates": False},
            )
            if self._candidate.surface_ids:
                offset = await _load_telegram_offset(self._candidate.key)

            while True:
                try:
                    params: dict[str, Any] = {
                        "timeout": 30,
                        "allowed_updates": json.dumps(
                            ["message", "edited_message", "callback_query"]
                        ),
                    }
                    if offset is not None:
                        params["offset"] = offset

                    data = await self._telegram_api(
                        client, base_url, "getUpdates", params
                    )
                    conflict_deadline = None
                    for update in data.get("result") or []:
                        update_id = update.get("update_id")
                        _kinds = [
                            key
                            for key in (
                                "message",
                                "edited_message",
                                "channel_post",
                                "callback_query",
                                "my_chat_member",
                            )
                            if key in update
                        ]
                        _msg = (
                            update.get("message") or update.get("edited_message") or {}
                        )
                        _chat = _msg.get("chat") or {}
                        logger.debug("agent_surfaces.event_receiver_service.telegram_polling_received_update_id.observed", update_id=update_id)
                        logger.debug("agent_surfaces.event_receiver_service.telegram_raw_update_s.observed")
                        if isinstance(update_id, int):
                            offset = update_id + 1
                            if self._candidate.surface_ids:
                                await _store_telegram_offset(
                                    self._candidate.key, offset
                                )
                        await _publish_native_receiver_event(
                            source="telegram",
                            payload=update,
                            receiver_key=self._candidate.key,
                            surface_ids=self._candidate.surface_ids,
                        )
                except asyncio.CancelledError:
                    raise
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 409:
                        now = asyncio.get_running_loop().time()
                        if conflict_deadline is None:
                            conflict_deadline = now + _TELEGRAM_CONFLICT_GRACE_SECONDS
                            logger.debug('agent_surfaces.event_receiver_service.telegram_polling_hit_409_after.diagnostic')
                        if now < conflict_deadline:
                            await asyncio.sleep(5)
                            continue
                        logger.debug('agent_surfaces.event_receiver_service.telegram_polling_still_gets_409.diagnostic')
                        return
                    logger.debug('agent_surfaces.event_receiver_service.telegram_polling_receiver_s.diagnostic', exc_info=True)
                    await asyncio.sleep(5)
                except httpx.ReadTimeout:
                    # Expected during long polling: the 30s long-poll can
                    # occasionally exceed the read timeout due to network
                    # latency. Retry quietly without a noisy traceback.
                    logger.debug("agent_surfaces.event_receiver_service.telegram_polling_getupdates_read_timeout.timeout")
                    await asyncio.sleep(1)
                except Exception:
                    logger.debug('agent_surfaces.event_receiver_service.telegram_polling_receiver_s.diagnostic', exc_info=True)
                    await asyncio.sleep(5)

    async def _telegram_api(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        response = await client.post(f"{base_url}/{method}", data=params)
        response.raise_for_status()
        return response.json()


class SlackSocketReceiverRunner:
    def __init__(self, candidate: NativeReceiverCandidate) -> None:
        self._candidate = candidate

    async def run(self) -> None:
        app_token = str(self._candidate.credentials.get("app_token") or "").strip()
        if not app_token:
            logger.debug('agent_surfaces.event_receiver_service.slack_native_receiver_missing_app.diagnostic')
            return

        from slack_sdk.socket_mode.aiohttp import SocketModeClient
        from slack_sdk.socket_mode.request import SocketModeRequest
        from slack_sdk.socket_mode.response import SocketModeResponse
        from slack_sdk.web.async_client import AsyncWebClient

        client = SocketModeClient(
            app_token=app_token,
            web_client=AsyncWebClient(
                token=self._candidate.credentials.get("bot_token") or None
            ),
        )

        async def _listener(
            socket_client: SocketModeClient, req: SocketModeRequest
        ) -> None:
            await socket_client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id)
            )
            if req.type != "events_api":
                return
            await _publish_native_receiver_event(
                source="slack",
                payload=req.payload,
                receiver_key=self._candidate.key,
                surface_ids=self._candidate.surface_ids,
            )

        client.socket_mode_request_listeners.append(_listener)
        try:
            await client.connect()
            while True:
                await asyncio.sleep(3600)
        finally:
            await client.close()


async def _receiver_credentials(
    surface: AgentSurfaceEntity,
    account_port: SqlAlchemySurfaceAccountAdapter,
    account_cache: dict[UUID, dict[str, Any]],
) -> dict[str, Any] | None:
    if surface.account_id is None:
        if surface.surface_type is SurfacePlatform.TELEGRAM:
            if not surface_settings.telegram_bot_token:
                logger.debug('agent_surfaces.event_receiver_service.telegram_system_surface_exists_but.diagnostic')
                return None
            return {"bot_token": surface_settings.telegram_bot_token}
        return None

    if surface.account_id not in account_cache:
        account = await account_port.get_account(surface.account_id)
        if account is None:
            logger.debug('agent_surfaces.event_receiver_service.native_receiver_skipped_surface_s.diagnostic', account_id=surface.account_id)
            return None
        account_cache[surface.account_id] = dict(account.credentials or {})
    credentials = dict(account_cache[surface.account_id])

    if surface.surface_type is SurfacePlatform.SLACK:
        if surface.credential_mode is SurfaceCredentialMode.SYSTEM:
            credentials["app_token"] = surface_settings.slack_app_token
        else:
            credentials["app_token"] = _nested_credential(credentials, "app_token")
        credentials["bot_token"] = slack_access_token(credentials)
    return credentials


def _candidate_from_surface(
    surface: AgentSurfaceEntity,
    credentials: dict[str, Any],
) -> NativeReceiverCandidate | None:
    if surface.surface_type is SurfacePlatform.TELEGRAM:
        bot_token = str(credentials.get("bot_token") or "").strip()
        if not bot_token:
            logger.debug('agent_surfaces.event_receiver_service.telegram_native_receiver_skipped_surface.diagnostic')
            return None
        credential_label = str(surface.account_id) if surface.account_id else "system"
        return NativeReceiverCandidate(
            key=_receiver_key("telegram", credential_label, bot_token),
            platform=SurfacePlatform.TELEGRAM,
            surface_ids=(surface.id,),
            credential_label=credential_label,
            credentials=credentials,
        )

    if surface.surface_type is SurfacePlatform.SLACK:
        app_token = str(credentials.get("app_token") or "").strip()
        if not app_token:
            logger.debug('agent_surfaces.event_receiver_service.slack_native_receiver_skipped_surface.diagnostic')
            return None
        credential_label = (
            "system"
            if surface.credential_mode is SurfaceCredentialMode.SYSTEM
            else str(surface.account_id)
        )
        return NativeReceiverCandidate(
            key=_receiver_key("slack", credential_label, app_token),
            platform=SurfacePlatform.SLACK,
            surface_ids=(surface.id,),
            credential_label=credential_label,
            credentials=credentials,
        )
    return None


def _nested_credential(credentials: dict[str, Any], key: str) -> str | None:
    if credentials.get(key):
        return str(credentials[key])
    raw_response = credentials.get("raw_response") or {}
    if isinstance(raw_response, dict) and raw_response.get(key):
        return str(raw_response[key])
    return None


def _receiver_key(platform: str, label: str, secret: str) -> str:
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:24]
    return f"{platform}:{label}:{digest}"


def _lease_key(key: str) -> str:
    return f"agent_surfaces:native_receiver:{key}"


def _telegram_offset_key(key: str) -> str:
    return f"agent_surfaces:telegram_offset:{key}"


async def _load_telegram_offset(key: str) -> int | None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        raw = await redis.get(_telegram_offset_key(key))
        return int(raw) if raw else None
    except Exception:
        logger.debug("agent_surfaces.event_receiver_service.could_not_load_telegram_polling.observed", exc_info=True)
        return None
    finally:
        await redis.aclose()


async def _store_telegram_offset(key: str, offset: int) -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis.set(_telegram_offset_key(key), str(offset))
    except Exception:
        logger.debug("agent_surfaces.event_receiver_service.could_not_store_telegram_polling.observed", exc_info=True)
    finally:
        await redis.aclose()


async def _publish_native_receiver_event(
    *,
    source: str,
    payload: dict[str, Any],
    receiver_key: str | None,
    surface_ids: "tuple[UUID, ...] | None" = None,
) -> None:
    headers = {"x-lemma-surface-event-mode": "native_receiver"}
    if receiver_key:
        headers["x-lemma-surface-receiver-key"] = receiver_key
    provider_id = (
        payload.get("event_id")
        or payload.get("update_id")
        or payload.get("id")
        or hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()
    )
    source_event_id = f"{source}:native:{provider_id}"
    event = SurfaceWebhookReceivedEvent(
        event_id=stable_event_id({"event_id": source_event_id}),
        source=source,
        payload=payload,
        headers=headers,
        source_event_id=source_event_id,
        # Scope downstream ingress to the surfaces this bot actually serves, so a
        # custom bot's update can't be mis-attributed to another bot's surface.
        receiver_surface_ids=list(surface_ids) if surface_ids else None,
    )
    await EventPublisher.publish(event.stream_name(), event)
