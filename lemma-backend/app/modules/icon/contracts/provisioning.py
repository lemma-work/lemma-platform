"""The icon service a resource-owning module binds as its cleanup port.

Replaces `app/composition/icons.py`. Three per-request DI builders -- pod,
function and workflow -- call this, and none of them wants to know that an icon
lives in an object store: each types the result as `IconCleanupPort` and only
ever asks it to forget a URL.

`lru_cache` because the call takes no arguments and the service it returns holds
no request state, so one instance serves every builder. Before it was cached,
each request built a fresh service -- and with it a fresh object store.

A submodule rather than `contracts/__init__`, which is a leaf: this reaches the
service layer, and everything importing any icon contract would otherwise pay
for it.
"""

from __future__ import annotations

from functools import lru_cache

from app.modules.icon.contracts import IconCleanupPort
from app.modules.icon.services.icon_service import IconService


@lru_cache(maxsize=1)
def create_icon_service() -> IconCleanupPort:
    """The process-wide cleanup port.

    Callers that need the request's base URL construct ``IconService`` directly;
    that path is unaffected and shares the same cached store.
    """
    return IconService()


__all__ = ["create_icon_service"]
