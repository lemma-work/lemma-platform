"""This process's ``org`` hubs: one per watched organization, reaped when idle."""

from __future__ import annotations

import asyncio
import contextlib
import time
from uuid import UUID

from app.core.authorization.authz_epoch import pod_grants_epochs
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.live.config import LiveSettings, live_settings
from app.core.live.org_feeds import OrgFeeds
from app.core.live.org_hub import OrgHub
from app.core.live.org_standing import database_standing
from app.core.live.protocol import LiveUnavailable
from app.core.live.signals import signal_listeners
from app.core.live.topics import LiveRegistry, live_registry
from app.core.request_context import create_background_task


def feeds_of(registry: LiveRegistry) -> OrgFeeds:
    """The org feeds the modules installed; unavailable without a directory,
    since nothing else may say which pods an organization shows."""
    if registry.org_directory is None:
        raise LiveUnavailable("no module says which pods an organization shows")
    return OrgFeeds(
        directory=registry.org_directory,
        relays=tuple(registry.org_relays.values()),
        states=tuple(registry.org_states.values()),
    )


class OrgHubs:
    """Every ``org`` hub in this process, made on first use and stopped once
    nobody has watched it for ``LIVE_HUB_LINGER_SECONDS``."""

    def __init__(self, settings: LiveSettings) -> None:
        self._settings = settings
        self._hubs: dict[UUID, OrgHub] = {}
        self._reaper: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def hub(self, organization_id: UUID) -> OrgHub:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            # A hub's tasks belong to the loop that made it; a test that
            # started a fresh loop starts fresh hubs.
            self._hubs, self._reaper, self._loop = {}, None, loop
        hub = self._hubs.get(organization_id)
        if hub is None:
            hub = OrgHub(
                organization_id,
                feeds=feeds_of(live_registry()),
                standing=database_standing(
                    SessionUnitOfWorkFactory(async_session_maker), organization_id
                ),
                listeners=signal_listeners(),
                epochs_of=pod_grants_epochs,
                settings=self._settings,
                on_idle=self._schedule_reap,
            )
            self._hubs[organization_id] = hub
        return hub

    def hubs(self) -> dict[UUID, OrgHub]:
        return dict(self._hubs)

    def _schedule_reap(self, _hub: OrgHub) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = create_background_task(self._reap(), name="live-org-reaper")

    async def _reap(self) -> None:
        linger = self._settings.live_hub_linger_seconds
        while True:
            await asyncio.sleep(max(linger, 0.05))
            for organization_id, hub in list(self._hubs.items()):
                since = hub.idle_since
                if since is None or time.monotonic() - since < linger:
                    continue
                self._hubs.pop(organization_id, None)
                with contextlib.suppress(Exception):
                    await hub.stop()
            if not any(hub.idle_since is not None for hub in self._hubs.values()):
                return


_hubs = OrgHubs(live_settings)


def org_hubs() -> OrgHubs:
    return _hubs


__all__ = ["OrgHubs", "feeds_of", "org_hubs"]
