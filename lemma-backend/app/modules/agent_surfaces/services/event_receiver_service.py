from __future__ import annotations

import asyncio
import socket
import uuid
from collections.abc import Callable
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from app.core.infrastructure.redis.client import get_redis

from app.core.config import settings
from app.modules.agent_surfaces.config import surface_settings
from app.core.infrastructure.channels.channel_service import channel_service
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent_surfaces.services.telegram_polling_runner import (
    TelegramPollingReceiverRunner,
)
from app.core.log.log import get_logger
from app.core.request_context import create_background_task
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.infrastructure.adapters.account_adapter import (
    SqlAlchemySurfaceAccountAdapter,
)
from app.modules.agent_surfaces.platforms.slack.client import slack_access_token
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.services.native_receiver_base import (
    _publish_native_receiver_event,
    NativeReceiverCandidate,
    ReceiverRunnerFactory,
    receiver_key as _receiver_key,
)
from app.modules.agent_surfaces.services.resend_polling_receiver import (
    ResendPollingReceiverRunner,
    resend_candidate_from_surface,
    resend_receiver_credentials,
)

logger = get_logger(__name__)

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
        logger.debug(
            "agent_surfaces.event_receiver_service.could_not_publish_surface_receiver.observed",
            exc_info=True,
        )


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
            or surface_settings.enable_resend_polling_mode
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
            SurfacePlatform.RESEND: ResendPollingReceiverRunner,
        }

    async def run(self) -> None:
        # Holds a Pub/Sub subscription open, so a silent connection is normal.
        self._redis = get_redis(url=self._redis_url, blocking=True)
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
        # Shared client: release the reference, do not close the pool.
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
        if surface_settings.enable_resend_polling_mode:
            platforms.add(SurfacePlatform.RESEND)
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
            logger.debug(
                "agent_surfaces.event_receiver_service.native_surface_receiver_wakeup_listener.diagnostic",
                exc_info=True,
            )
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
            logger.debug(
                "agent_surfaces.event_receiver_service.native_surface_receiver_stopped_platform.diagnostic",
                exc_info=True,
            )
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


class SlackSocketReceiverRunner:
    def __init__(self, candidate: NativeReceiverCandidate) -> None:
        self._candidate = candidate

    async def run(self) -> None:
        app_token = str(self._candidate.credentials.get("app_token") or "").strip()
        if not app_token:
            logger.debug(
                "agent_surfaces.event_receiver_service.slack_native_receiver_missing_app.diagnostic"
            )
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
                logger.debug(
                    "agent_surfaces.event_receiver_service.telegram_system_surface_exists_but.diagnostic"
                )
                return None
            return {"bot_token": surface_settings.telegram_bot_token}
        if surface.surface_type is SurfacePlatform.RESEND:
            return resend_receiver_credentials()
        return None

    if surface.account_id not in account_cache:
        account = await account_port.get_account(surface.account_id)
        if account is None:
            logger.debug(
                "agent_surfaces.event_receiver_service.native_receiver_skipped_surface_s.diagnostic",
                account_id=surface.account_id,
            )
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
            logger.debug(
                "agent_surfaces.event_receiver_service.telegram_native_receiver_skipped_surface.diagnostic"
            )
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
            logger.debug(
                "agent_surfaces.event_receiver_service.slack_native_receiver_skipped_surface.diagnostic"
            )
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

    if surface.surface_type is SurfacePlatform.RESEND:
        return resend_candidate_from_surface(surface, credentials)
    return None


def _nested_credential(credentials: dict[str, Any], key: str) -> str | None:
    if credentials.get(key):
        return str(credentials[key])
    raw_response = credentials.get("raw_response") or {}
    if isinstance(raw_response, dict) and raw_response.get(key):
        return str(raw_response[key])
    return None


def _lease_key(key: str) -> str:
    return f"agent_surfaces:native_receiver:{key}"
