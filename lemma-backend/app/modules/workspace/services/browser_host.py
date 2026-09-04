"""Serving the sandbox browser at an origin of its own.

The dashboard `agent-browser` ships is a Next.js app: every asset it names is
absolute (`/_next/static/...`, `/favicon.ico`), so under a path prefix the
browser asks the API root for them and gets nothing. No stylesheet, no scripts,
and a page that sits at "Disconnected" forever because it never hydrated. Its
own help says as much — every proxying example it gives is an origin, not a
path.

So the browser gets a host: ``<code>.<browser_base_domain>``. Everything the
dashboard asks for then resolves against that host, and
:class:`BrowserHostRoutingMiddleware` rewrites it onto the port proxy that
already knows how to reach the sandbox.

**Why a code rather than the grant itself.** A signed port grant is ~135
characters and a DNS label stops at 63, so the token cannot be the label. The
code is an opaque handle in Redis pointing at the grant, with the same expiry —
the shape ``/s/`` short links already use. It leaks nothing: knowing a code
without the record behind it is knowing nothing, and the record expires with the
grant it wraps.
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timezone

from redis.asyncio import Redis

from app.core.config import settings
from app.core.infrastructure.redis.client import get_redis

_KEY_PREFIX = "workspace:browser-host:v1"

#: Long enough not to be guessed, short enough for a DNS label with room to
#: spare. 20 base32 characters is ~100 bits.
_CODE_BYTES = 13


class BrowserHostCodeStore:
    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url or settings.redis_url
        self._redis: Redis | None = None
        self._lock = asyncio.Lock()

    async def _get_redis(self) -> Redis:
        if self._redis is not None:
            return self._redis
        async with self._lock:
            if self._redis is None:
                self._redis = get_redis(url=self._redis_url)
        return self._redis

    @staticmethod
    def _key(code: str) -> str:
        return f"{_KEY_PREFIX}:{code}"

    async def mint(self, token: str, *, expires_at: datetime) -> str:
        """A host label standing in for one signed grant."""
        ttl = max(1, int((expires_at - datetime.now(timezone.utc)).total_seconds()))
        code = secrets.token_hex(_CODE_BYTES)
        redis = await self._get_redis()
        # The code dies with the grant it wraps, so a stale host cannot outlive
        # the access it was minted for.
        await redis.set(self._key(code), token, ex=ttl)
        return code

    async def resolve(self, code: str) -> str | None:
        redis = await self._get_redis()
        value = await redis.get(self._key(code))
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)


def browser_code_from_host(host: str) -> str | None:
    """The code encoded in ``host``, or None when this is not a browser host.

    Mirrors ``apps/api/host_routing.py``: the code is the single left-most label
    in front of the configured base domain. The bare base domain and multi-level
    hosts are not browsers.
    """
    from app.modules.workspace.config import workspace_settings

    base = workspace_settings.browser_base_domain
    if not base:
        return None
    host_no_port = host.split(":", 1)[0].strip().lower()
    base_no_port = base.split(":", 1)[0].strip().lower()
    if not host_no_port or not base_no_port:
        return None
    suffix = f".{base_no_port}"
    if not host_no_port.endswith(suffix):
        return None
    label = host_no_port[: -len(suffix)]
    if not label or "." in label:
        return None
    return label
