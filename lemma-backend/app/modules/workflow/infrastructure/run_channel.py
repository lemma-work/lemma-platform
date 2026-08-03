"""Realtime fan-out for workflow run state.

A run was the only live thing in the product that did not stream: agent
conversations and bundle imports both do, while every viewer of a running
workflow paid a GET every two seconds. This is the thin layer that fixes it.

Two deliberate choices:

* **Publish after commit, never before.** A subscriber must not be able to see
  state the database has not accepted. Every publish site in the engine sits
  after `uow.commit()`.
* **Send the whole run, not a diff.** Run payloads are small, and a full
  snapshot makes the client a pure replace-state reducer — which in turn makes
  reconnect trivially correct rather than a resync problem. The stream is a
  latency optimization over polling, not a second source of truth.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from app.core.domain.realtime import RealtimeChannel
from app.core.infrastructure.channels.channel_service import get_channel_service
from app.core.log.log import get_logger

logger = get_logger(__name__)


def workflow_run_channel(run_id: UUID | str) -> str:
    return f"workflow-run:{run_id}"


def encode_run_chunk(*, event_type: str, data: Any) -> str:
    """Server-sent event framing, matching the agent stream's shape."""
    payload = json.dumps({"type": event_type, "data": data}, default=str)
    return f"data: {payload}\n\n"


async def publish_run_state(
    run_id: UUID,
    payload: dict[str, Any],
    *,
    terminal: bool = False,
    channel_service: RealtimeChannel | None = None,
) -> None:
    """Announce the current state of a run to anyone watching.

    Best effort by design. Realtime channels carry no delivery guarantee and the
    client keeps polling as a fallback, so a failure here must never surface as
    a failed run — the state is already committed.
    """
    try:
        service = channel_service or await get_channel_service()
        await service.publish(
            workflow_run_channel(run_id),
            json.dumps(
                {
                    "type": "completed" if terminal else "run",
                    "data": payload,
                },
                default=str,
            ),
        )
    except Exception:
        logger.debug("workflow.run.publish_failed", run_id=str(run_id), exc_info=True)
