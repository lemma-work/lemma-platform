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
    from app.events import streaq_worker

    logger = get_logger("app.worker")
    try:
        streaq_worker.run_sync()
    except Exception:
        logger.error("worker.startup.failed", exc_info=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
