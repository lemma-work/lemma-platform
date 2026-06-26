"""Separate FastAPI app for scheduler service.

This runs as a separate singleton pod and provides APIs for scheduling jobs.
The scheduler emits events via FastStream when jobs fire, which are then
handled by the main application pod.
"""

from __future__ import annotations
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.modules.schedule.scheduler.api.scheduler_controller import (
    router as scheduler_router,
)
from app.modules.schedule.scheduler.scheduler_service import get_scheduler_service
from app.core.config import settings
from app.core.log.log import setup_logging, get_logger
from app.version import API_VERSION

logger = get_logger(__name__)


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
    scheduler = get_scheduler_service()
    await scheduler.start()
    logger.info("Scheduler service started")

    yield

    # Shutdown
    scheduler = get_scheduler_service()
    await scheduler.shutdown()
    logger.info("Scheduler service stopped")


# Create FastAPI app for scheduler
app = FastAPI(
    title="Scheduler API",
    version=API_VERSION,
    description="API for managing scheduled jobs. Jobs emit events via FastStream when they fire.",
    lifespan=lifespan,
    debug=settings.debug,
)

# Configure CORS
# Check if settings.cors_origins is a list or string, typically list in Pydantic settings
origins = settings.cors_origins
if isinstance(origins, str):
    origins = [o.strip() for o in origins.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("")
async def index() -> str:
    return "Scheduler API is ready."


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    scheduler = get_scheduler_service()
    status = "healthy" if scheduler._started else "starting"
    return {"status": status, "message": "Scheduler API is running"}


app.include_router(scheduler_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.scheduler:app", host="0.0.0.0", port=8001, reload=True)
