"""Canonical helper for running blocking work off the event loop.

The worker is a single event loop shared by streaq tasks and every FastStream
subscriber. Any synchronous call that blocks (CPU-bound work, a sync SDK, a
blocking network client) freezes *all* of them until it returns. The rule is:
blocking work goes through :func:`run_blocking`, never directly on the loop.

Two things this centralizes:

1. **Named capacity limiters.** ``anyio.to_thread.run_sync`` without a limiter
   shares one process-wide pool; a burst of one kind of blocking call (say 20
   concurrent connector HTTP calls) can then starve unrelated offloads (PDF
   rasterization, embeddings). Partitioning by workload class — ``cpu_bound``,
   ``external_http``, ``crypto`` — bounds each independently so one can't drain
   the others. ``asyncio.to_thread`` (a *different* default executor) is
   replaced by this so there is a single, coherent, bounded system.

2. **Thread-pool headroom.** :func:`configure_thread_pool` raises anyio's global
   default limiter at startup so the residual un-limited offloads (e.g. the
   embedder / reranker) and the named limiters (which sum above the default 40)
   all have room.

Usage::

    from app.core.concurrency.offload import run_blocking

    result = await run_blocking(cpu_heavy_fn, arg, limiter="cpu_bound")
"""

from __future__ import annotations

from functools import partial
from typing import Callable, TypeVar

import anyio
import anyio.to_thread

from app.core.config import settings
from app.core.log.log import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

# Workload class -> settings attribute holding its limiter size.
_LIMITER_SETTINGS: dict[str, str] = {
    "cpu_bound": "offload_cpu_bound_limit",
    "external_http": "offload_external_http_limit",
    "crypto": "offload_crypto_limit",
}

_limiters: dict[str, anyio.CapacityLimiter] = {}


def get_limiter(name: str) -> anyio.CapacityLimiter:
    """Return the process-wide capacity limiter for a workload class (lazy)."""
    limiter = _limiters.get(name)
    if limiter is None:
        attr = _LIMITER_SETTINGS.get(name)
        if attr is None:
            raise ValueError(
                f"Unknown offload limiter {name!r}; "
                f"expected one of {sorted(_LIMITER_SETTINGS)}"
            )
        limiter = anyio.CapacityLimiter(max(1, getattr(settings, attr)))
        _limiters[name] = limiter
    return limiter


async def run_blocking(
    fn: Callable[..., T],
    *args: object,
    limiter: str = "cpu_bound",
    **kwargs: object,
) -> T:
    """Run a blocking callable in a worker thread, gated by a named limiter.

    ``limiter`` selects the workload class ("cpu_bound" for CPU work like
    chunking / zipping / tokenizing, "external_http" for blocking network SDKs,
    "crypto" for KMS). Keyword args are bound before dispatch.
    """
    return await anyio.to_thread.run_sync(
        partial(fn, *args, **kwargs), limiter=get_limiter(limiter)
    )


def configure_thread_pool() -> None:
    """Raise anyio's global default thread limiter to ``offload_total_threads``.

    Must be called from within a running event loop (the default limiter is
    resolved per async backend). Idempotent; safe to call in both the worker and
    API lifespans.
    """
    try:
        limiter = anyio.to_thread.current_default_thread_limiter()
        limiter.total_tokens = settings.offload_total_threads
        logger.debug("concurrency.offload.configured_offload_thread_pool.observed")
    except Exception:  # pragma: no cover - defensive; never block startup
        logger.debug(
            'concurrency.offload.could_not_configure_offload_thread.diagnostic'
        )


def reset_limiters_for_test() -> None:
    """Test hook: drop cached limiters so they rebuild from current settings."""
    _limiters.clear()
