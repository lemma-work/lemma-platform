"""This process's channels hubs: one per watched pod, reaped when idle."""

from __future__ import annotations

import asyncio
import contextlib
import time
from uuid import UUID

from app.core.authorization.authz_epoch import pod_grants_epoch
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.live.config import LiveSettings, live_settings
from app.core.live.signals import signal_listeners
from app.core.live.topics import live_registry
from app.core.request_context import create_background_task
from app.modules.channels.services.live.hub import ChannelsHub


class ChannelsHubs:
    """Every channels hub in this process, made on first use and stopped once
    nobody has watched it for ``LIVE_HUB_LINGER_SECONDS``."""

    def __init__(self, settings: LiveSettings) -> None:
        self._settings = settings
        self._hubs: dict[UUID, ChannelsHub] = {}
        self._reaper: asyncio.Task[None] | None = None

    def hub(self, pod_id: UUID) -> ChannelsHub:
        hub = self._hubs.get(pod_id)
        if hub is None:
            hub = ChannelsHub(
                pod_id,
                uow_factory=SessionUnitOfWorkFactory(async_session_maker),
                listeners=signal_listeners(),
                streams=live_registry(),
                epoch_of=pod_grants_epoch,
                settings=self._settings,
                on_idle=self._schedule_reap,
            )
            self._hubs[pod_id] = hub
        return hub

    def hubs(self) -> dict[UUID, ChannelsHub]:
        return dict(self._hubs)

    def _schedule_reap(self, _hub: ChannelsHub) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = create_background_task(
                self._reap(), name="live-channels-reaper"
            )

    async def _reap(self) -> None:
        linger = self._settings.live_hub_linger_seconds
        while True:
            await asyncio.sleep(max(linger, 0.05))
            for hub in list(self._hubs.values()):
                # Re-checked as it goes: a subscriber may have joined meanwhile.
                since = hub.idle_since
                if since is None or time.monotonic() - since < linger:
                    continue
                if self._hubs.get(hub.pod_id) is hub:
                    self._hubs.pop(hub.pod_id)
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


_hubs: ChannelsHubs | None = None
_hubs_loop: asyncio.AbstractEventLoop | None = None


def channels_hubs() -> ChannelsHubs:
    """This process's hubs, bound to the running event loop (a test that
    starts a fresh loop gets fresh hubs)."""
    global _hubs, _hubs_loop
    loop = asyncio.get_running_loop()
    if _hubs is None or _hubs_loop is not loop:
        _hubs, _hubs_loop = ChannelsHubs(live_settings), loop
    return _hubs


__all__ = ["ChannelsHubs", "channels_hubs"]
