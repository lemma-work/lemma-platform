"""``python -m app.worker``: the standalone worker, running the work runtime.

Logging is set up before anything else is imported, so every line the process
writes goes through the owned pipeline. Then ``run_worker`` enters
``worker_services`` (``app/core/infrastructure/work/process.py``) for the
module list -- the open-source one here; lemma-cloud passes its own -- and
waits for a signal.

SIGTERM or SIGINT starts the drain at once: claiming stops, each running job
sees ``ctx.stopping`` at its next safe point and is handed back uncharged, and
each class waits at most its own grace (``WORK_SHUTDOWN_GRACE_JSON``; the
interactive class longest, so an agent turn can reach a step boundary) before
what is still running is released. Then the process exits 0. The platform's
termination grace must exceed the longest class grace plus teardown.

``WORK_CLASSES`` selects the classes this process runs (all by default), which
is how an ingestion-only or receivers-only deployment is made.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from app.core.config import settings
from app.core.log.log import get_logger, setup_logging, validate_release_identity

if TYPE_CHECKING:
    from app.core.registry.contract import LemmaModule


def register_worker_modules(modules: Sequence[LemmaModule] | None = None) -> None:
    """Register what a worker for ``modules`` runs, without running it (tests)."""
    from app.core.infrastructure.work.process import (
        register_worker_modules as register,
    )
    from app.core.registry.installed import OSS_MODULES

    register(OSS_MODULES if modules is None else modules)


async def run_worker(modules: Sequence[LemmaModule]) -> None:
    """Run until SIGTERM/SIGINT, then drain within each class's grace."""
    import asyncio
    import signal

    from app.core.infrastructure.work.process import worker_services

    logger = get_logger("app.worker")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    async with worker_services(modules) as runtime:

        def _on_signal(signum: int) -> None:
            logger.info("worker.signal.received", signal=signal.Signals(signum).name)
            runtime.begin_stop()
            stop.set()

        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, _on_signal, signum)
        try:
            await stop.wait()
            await runtime.stop()
        finally:
            for signum in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(signum)


def main(modules: Sequence[LemmaModule] | None = None) -> None:
    setup_logging(
        settings.environment,
        service_name="lemma-worker",
        json_logs=settings.json_logs_enabled,
        log_level=settings.log_level,
    )
    validate_release_identity(settings.environment)

    import asyncio

    from app.core.registry.installed import OSS_MODULES

    logger = get_logger("app.worker")
    try:
        asyncio.run(run_worker(OSS_MODULES if modules is None else modules))
    except Exception:
        logger.error("worker.startup.failed", exc_info=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
