"""Bind resource modules to the icon storage implementation."""

from functools import lru_cache

from app.modules.icon.contracts import IconCleanupPort
from app.modules.icon.services.icon_service import IconService


@lru_cache(maxsize=1)
def create_icon_service() -> IconCleanupPort:
    """The process-wide cleanup port.

    Four per-request DI builders call this (pod, function, workflow, and the
    datastore file service). It takes no arguments and the service it returns
    holds no request state, so one instance serves all of them; before this it
    was a fresh service — and a fresh object store — on every request.

    Callers that need the request's base URL construct ``IconService`` directly;
    that path is unaffected and now shares the same cached store.
    """
    return IconService()
