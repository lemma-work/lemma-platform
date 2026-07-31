"""Adapter for queueing schedule LLM-filter tasks in streaq."""

from __future__ import annotations

from typing import Any, Dict
from uuid import UUID

from app.core.domain.job_queue import JobQueuePort
from app.core.infrastructure.jobs.streaq_job_queue import get_streaq_job_queue
from app.modules.schedule.domain.interfaces import ScheduleFilterTaskQueue


class StreaqScheduleFilterTaskQueue(ScheduleFilterTaskQueue):
    """Queue LLM filter jobs for deferred schedule processing."""

    def __init__(self, job_queue: JobQueuePort | None = None):
        self._job_queue = job_queue or get_streaq_job_queue()

    async def enqueue(
        self,
        *,
        schedule_id: UUID | None = None,
        payload: Dict[str, Any],
        metadata: Dict[str, Any],
        source_event_id: str,
    ) -> None:
        if schedule_id is None:
            raise ValueError("schedule_id is required")
        await self._job_queue.enqueue(
            "handle_llm_filter_task",
            schedule_id=str(schedule_id),
            payload=payload,
            metadata=metadata,
            source_event_id=source_event_id,
            _job_id=f"schedule-filter:{schedule_id}:{source_event_id}",
        )
