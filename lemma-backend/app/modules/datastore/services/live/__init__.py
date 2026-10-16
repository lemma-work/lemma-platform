"""The pod live socket's server side: hubs, sockets, access and limits."""

from __future__ import annotations

import asyncio

from app.core.authorization.authz_epoch import pod_authz_epoch
from app.core.infrastructure.channels.channel_service import get_channel_service
from app.modules.datastore.config_live import live_settings
from app.modules.datastore.services.live.access import TableLiveAccess
from app.modules.datastore.services.live.feed import ChangeLogFeed
from app.modules.datastore.services.live.registry import LiveHubRegistry

_registry: LiveHubRegistry | None = None
_registry_loop: asyncio.AbstractEventLoop | None = None


def live_hubs() -> LiveHubRegistry:
    """This process's hubs, bound to the running event loop.

    A hub's tasks, locks and Pub/Sub lease belong to the loop that made them;
    a registry from another loop (a test that started a fresh one) is dropped
    rather than reused.
    """
    global _registry, _registry_loop
    loop = asyncio.get_running_loop()
    if _registry is None or _registry_loop is not loop:
        _registry = LiveHubRegistry(
            feed_factory=ChangeLogFeed,
            access_factory=TableLiveAccess,
            channel_factory=get_channel_service,
            epoch_of=pod_authz_epoch,
            settings=live_settings,
        )
        _registry_loop = loop
    return _registry


__all__ = ["live_hubs"]
