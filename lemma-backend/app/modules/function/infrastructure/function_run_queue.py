"""Streaq adapter for the asynchronous function-run queue."""

from __future__ import annotations

import asyncio
from uuid import UUID

from app.core.infrastructure.jobs.streaq_job_queue import SharedStreaqJobQueue
from app.modules.function.domain.errors import FunctionRunQueueUnavailable
from app.modules.function.domain.identities import function_run_job_id


class StreaqFunctionRunQueue:
    """Publish function runs directly to Streaq with atomic same-ID deduplication."""

    def __init__(self, queue: SharedStreaqJobQueue) -> None:
        self._queue = queue

    async def enqueue(self, run_id: UUID) -> str:
        job_id = function_run_job_id(run_id)
        try:
            await self._queue.enqueue(
                "process_function_run",
                run_id=str(run_id),
                _job_id=job_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise FunctionRunQueueUnavailable(
                "function run queue did not confirm publication"
            ) from exc
        return job_id
