"""Separate FastAPI app for scheduler service.

This runs as a separate singleton pod and provides APIs for scheduling jobs.
The scheduler emits events via FastStream when jobs fire, which are then
handled by the main application pod.
"""

from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.modules.schedule.scheduler.api.scheduler_controller import (
    router as scheduler_router,
)
from app.modules.schedule.scheduler.scheduler_service import get_scheduler_service
from app.core.config import settings
from app.core.log.log import setup_logging, get_logger, validate_release_identity
from app.core.infrastructure.db.session import get_engine
from app.core.observability.telemetry import (
    init_telemetry,
    instrument_database_engine,
    instrument_fastapi_app,
)
from app.version import API_VERSION

logger = get_logger(__name__)

# Low-rate structured heartbeat for remote absence detection of this singleton
# background process. At 5 min this is <600 records/48h. service.version is
# attached by the logging context.
_HEARTBEAT_INTERVAL_SECONDS = 300.0


async def _scheduler_heartbeat_loop() -> None:
    """Emit ``scheduler.heartbeat`` every 5 min while the scheduler loop is healthy."""
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
        logger.info("scheduler.heartbeat")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup
    setup_logging(
        settings.environment,
        service_name="lemma-scheduler",
        json_logs=settings.json_logs_enabled,
        log_level=settings.log_level,
    )
    validate_release_identity(settings.environment)
    scheduler = get_scheduler_service()
    await scheduler.start()
    logger.info("Scheduler service started")
    # One stable startup event after initialization succeeds.
    logger.info("service.started")
    heartbeat_task = asyncio.create_task(_scheduler_heartbeat_loop())

    yield

    # Shutdown
    if not heartbeat_task.done():
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except BaseException:
            pass
    scheduler = get_scheduler_service()
    await scheduler.shutdown()
    logger.info("Scheduler service stopped")


# Create FastAPI app for scheduler
app = FastAPI(
    title="Scheduler API",
    version=API_VERSION,
    description="API for managing scheduled jobs. Jobs emit events via FastStream when they fire.",
    lifespan=lifespan,
    # Never enable FastAPI debug on the scheduler sidecar: it is an internal
    # service and debug=True would leak tracebacks on error responses.
    debug=False,
)
init_telemetry(service_name="lemma-scheduler")
instrument_database_engine(get_engine())
instrument_fastapi_app(app)

# Configure CORS
# This is an internal service-to-service API (only the backend's SchedulerAPIClient
# calls it); no browser talks to it, so keep the surface minimal rather than the
# previous wildcard methods/headers.
origins = settings.cors_origins
if isinstance(origins, str):
    origins = [o.strip() for o in origins.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.get("")
async def index() -> str:
    return "Scheduler API is ready."


@app.get("/health/live", include_in_schema=False)
@app.get("/livez", include_in_schema=False)
@app.get("/health", include_in_schema=False)
async def health_live() -> dict[str, str]:
    """Liveness: process/event-loop check only (no DB/network)."""
    return {"status": "ok"}


@app.get("/health/ready", include_in_schema=False)
async def health_ready():
    """Readiness: scheduler started + DB reachable. 503 when not ready."""
    import asyncio as _asyncio

    from sqlalchemy import text
    from fastapi.responses import JSONResponse

    from app.core.infrastructure.db.session import get_engine

    scheduler = get_scheduler_service()
    scheduler_ok = bool(scheduler._started)

    async def _db_ok() -> bool:
        try:
            engine = get_engine()
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    db_ok = False
    try:
        db_ok = await _asyncio.wait_for(_db_ok(), timeout=1.0)
    except Exception:
        db_ok = False

    components = {
        "scheduler": "ok" if scheduler_ok else "starting",
        "db": "ok" if db_ok else "down",
    }
    ready = scheduler_ok and db_ok
    return JSONResponse(
        {"status": "ready" if ready else "not_ready", "components": components},
        status_code=200 if ready else 503,
    )


app.include_router(scheduler_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.scheduler:app", host="0.0.0.0", port=8001, reload=True)
