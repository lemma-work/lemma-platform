"""Production Streaq worker entrypoint with owned logging from process start."""

from __future__ import annotations

from app.core.config import settings
from app.core.log.log import get_logger, setup_logging, validate_release_identity


def main() -> None:
    setup_logging(
        settings.environment,
        service_name="lemma-worker",
        json_logs=settings.json_logs_enabled,
        log_level=settings.log_level,
    )
    validate_release_identity(settings.environment)

    # Import only after the process-owned logging pipeline is installed. This
    # also avoids Streaq CLI's late dictConfig call and raw startup/traceback
    # output; the companion cloud wrapper invokes this module directly.
    import asyncio

    import app.events  # noqa: F401 — registers every task/cron on its lane
    from app.core.infrastructure.jobs.streaq_runtime import run_worker_lanes

    logger = get_logger("app.worker")
    try:
        # Runs whichever lanes WORKER_LANES selects (all of them by default), so
        # a single-process deployment is unchanged while a split deployment can
        # scale ingestion independently of latency-sensitive work.
        asyncio.run(run_worker_lanes())
    except Exception:
        logger.error("worker.startup.failed", exc_info=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
