from __future__ import annotations

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
    try:
        await observer.on_run_failed(conversation, error)
    except Exception:
        logger.debug(
            "agent.agent_runner_service.agent_run_observer_failure_delivery.diagnostic",
            agent_run_id=agent_run_id,
        )
