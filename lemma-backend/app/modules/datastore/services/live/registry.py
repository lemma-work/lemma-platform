"""Every pod hub in this process."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from uuid import UUID

from app.core.domain.realtime import RealtimeChannel
from app.core.request_context import create_background_task
from app.modules.datastore.config_live import LiveSettings
from app.modules.datastore.domain.ports import LiveAccessPort, LiveChangeFeedPort
from app.modules.datastore.services.live.hub import EpochReader, PodHub


class LiveHubRegistry:
    """Every pod hub in this process, created on first use."""

    def __init__(
        self,
        *,
        feed_factory: Callable[[], LiveChangeFeedPort],
        access_factory: Callable[[], LiveAccessPort],
        channel_factory: Callable[[], Awaitable[RealtimeChannel]],
        epoch_of: EpochReader,
        settings: LiveSettings,
    ) -> None:
        self._feed_factory = feed_factory
        self._access_factory = access_factory
        self._channel_factory = channel_factory
        self._epoch_of = epoch_of
        self._settings = settings
        self._hubs: dict[UUID, PodHub] = {}
        self._reaper: asyncio.Task[None] | None = None

    @property
    def settings(self) -> LiveSettings:
        return self._settings

    def hubs(self) -> dict[UUID, PodHub]:
        return dict(self._hubs)

    async def hub(self, pod_id: UUID) -> PodHub:
        hub = self._hubs.get(pod_id)
        if hub is None:
            hub = PodHub(
                pod_id,
                feed=self._feed_factory(),
                access=self._access_factory(),
                channel=await self._channel_factory(),
                epoch_of=self._epoch_of,
                settings=self._settings,
                on_idle=self._schedule_reap,
            )
            self._hubs[pod_id] = hub
        return hub

    def _schedule_reap(self, _hub: PodHub) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = create_background_task(self._reap(), name="live-hub-reaper")

    async def _reap(self) -> None:
        """Stop hubs that have had no subscribers for the linger period."""
        linger = self._settings.live_hub_linger_seconds
        while True:
            await asyncio.sleep(max(linger, 0.05))
            now = time.monotonic()
            idle = [
                hub
                for hub in self._hubs.values()
                if hub.idle_since is not None and now - hub.idle_since >= linger
            ]
            for hub in idle:
                self._hubs.pop(hub.pod_id, None)
                await hub.stop()
            if not any(hub.idle_since is not None for hub in self._hubs.values()):
                return

    async def close(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper
            self._reaper = None
        hubs, self._hubs = list(self._hubs.values()), {}
        for hub in hubs:
            await hub.stop()
