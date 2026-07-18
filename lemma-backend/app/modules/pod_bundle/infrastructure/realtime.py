"""Transient realtime publishing for pod bundle job progress.

Best-effort by design (mirrors ``app/modules/agent/services/realtime.py``):
event delivery never gates job progress. The durable picture is always the
Redis state document — SSE subscribers get a ``snapshot`` frame on connect
and use ``seq`` to discard stale live frames, so a dropped publish costs at
most one UI refresh interval.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.domain.realtime import RealtimeChannel
from app.core.infrastructure.channels.channel_service import (
    get_channel_service,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


def bundle_job_channel(job_id: UUID) -> str:
    return f"pod-bundle:events:{job_id}"


async def publish_bundle_event(
    job_id: UUID,
    payload: dict[str, Any],
    *,
    channel_service: RealtimeChannel | None = None,
) -> None:
    try:
        service = channel_service or await get_channel_service()
        await service.publish(bundle_job_channel(job_id), payload)
    except Exception:
        logger.debug(
            'pod_bundle.realtime.publishing_pod_bundle_realtime_event.diagnostic',
            job_id=job_id,
        )


def status_payload(status: str, seq: int) -> dict[str, Any]:
    return {"type": "status", "status": status, "seq": seq}


def step_payload(step: dict[str, Any], seq: int) -> dict[str, Any]:
    return {"type": "step", "step": step, "seq": seq}


def progress_payload(done: int, total: int, seq: int, **extra: Any) -> dict[str, Any]:
    return {"type": "progress", "done": done, "total": total, "seq": seq, **extra}


def completed_payload(status: str, seq: int, **extra: Any) -> dict[str, Any]:
    return {"type": "completed", "status": status, "seq": seq, **extra}


def error_payload(message: str, seq: int) -> dict[str, Any]:
    return {"type": "error", "message": message, "seq": seq}
