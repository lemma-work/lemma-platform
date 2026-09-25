"""Name every startup step and how long it took.

A calm API boot measured ~80s to a bound port: ~15s of imports, then ~45s in
the lifespan with nothing logged between the first line and ``service.started``.
Nothing could say which step it was, because no step said anything. Kubernetes
kills a pod that has not bound its port by the liveness deadline, so a boot that
drifts past it restarts, and the restart is just as slow -- the length of this
window is a correctness property, not a nicety.

One info line per step is cheap (a boot has a few dozen) and turns "startup is
slow" into "this step is slow".
"""

from __future__ import annotations

import gc
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.log.log import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def startup_step(step: str, *, service: str) -> AsyncIterator[None]:
    """Log ``service.startup.step`` with the step's duration when it ends.

    Logged on failure too, with ``ok=False``: the step that raised is exactly
    the one somebody reading a crash-looping boot needs to find.
    """
    started = time.monotonic()
    ok = False
    try:
        yield
        ok = True
    finally:
        logger.info(
            "service.startup.step",
            service=service,
            step=step,
            ok=ok,
            duration_ms=round((time.monotonic() - started) * 1000, 1),
        )


def finish_startup(boot_started: float) -> tuple[float, int]:
    """Freeze the startup heap; return (startup ms, frozen object count).

    Modules, routes, schemas and clients live as long as the process, yet every
    full collection rescans them. The dominant multi-second loop stall in
    production was exactly that -- ``sqlalchemy ... _target_gced`` on top of the
    stack, a weakref callback fired mid-collection -- and a collection's cost
    grows with what it has to walk. Call once, after startup, before serving,
    and pair it with :func:`release_startup_heap` when the lifespan ends.
    """
    gc.collect()
    gc.freeze()
    return round((time.monotonic() - boot_started) * 1000, 1), gc.get_freeze_count()


def release_startup_heap() -> None:
    """Hand frozen objects back to the collector when a lifespan ends.

    In production the process exits with it. Where it does not -- tests that
    start several apps in one process, an API with an embedded worker -- what
    the ended lifespan built would otherwise stay frozen, and a cycle among it
    would never be collected.
    """
    gc.unfreeze()
