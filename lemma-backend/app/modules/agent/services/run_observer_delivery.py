from __future__ import annotations

import asyncio
from typing import Any

from app.core.log.log import get_logger

logger = get_logger(__name__)


async def notify_run_failed(
    observer: Any,
    conversation: Any,
    error: BaseException,
    agent_run_id: Any,
) -> None:
    if observer is None or not isinstance(error, Exception):
        return
    result = await asyncio.gather(
        observer.on_run_failed(conversation, error),
        return_exceptions=True,
    )
    if result and isinstance(result[0], asyncio.CancelledError):
        raise result[0]
    if result and isinstance(result[0], BaseException):
        logger.debug(
            "agent.agent_runner_service.agent_run_observer_failure_delivery.diagnostic",
            agent_run_id=agent_run_id,
        )
