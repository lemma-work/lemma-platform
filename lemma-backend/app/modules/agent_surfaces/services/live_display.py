"""A run's live stream, recorded so the run's reply can finish it in place.

On Slack a run's answer streams into one message as the model writes it, and
the answer's delivery closes that same message with the rest of the answer --
one message in the thread, not a stream that vanishes and a second message
that repeats it. The stream belongs to the run's process (its observer opens
and writes it); the answer is sent by the channel delivery executor
(``run_answer_sends``), which may run anywhere and later. So the observer
records the stream here as it goes:

* where it is (the ``ProgressHandle``) and what of the answer it already shows;
* ``sealed`` once the run has finished and the observer has flushed the last
  of it and let go -- nothing else will be written to it but the answer.

The sender reads the record for a run's answer (``take_live_display``), waits
a moment for the seal when the observer is still finishing, and closes the
stream with what is left, recording the stream's message as the delivery's;
without a record, or when closing fails, it posts the answer as a new message
and takes any stream down first.

Display state, so Redis with a TTL, like decision-card handles: losing a record
costs the person one extra message, never the answer. Every call here is best
effort and never raises.
"""

from __future__ import annotations

import asyncio
import time
from uuid import UUID

from pydantic import BaseModel, ValidationError
from redis.exceptions import RedisError

from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger
from app.modules.agent_surfaces.caches import LIVE_DISPLAYS, surface_caches
from app.modules.agent_surfaces.domain.models import ProgressHandle, ProgressHandleKind

logger = get_logger(__name__)

_STORE_ERRORS = (RedisError, OSError)

#: How long the sender waits for a still-finishing observer to seal the stream.
#: Past it the observer is presumed gone and the stream is closed with what the
#: record says it shows.
SEAL_WAIT_SECONDS = 10.0
_SEAL_POLL_SECONDS = 0.2


class LiveDisplay(BaseModel):
    """Where a run's live stream is and what of the answer it already shows."""

    conversation_id: UUID
    kind: ProgressHandleKind
    message_id: str
    channel_id: str | None = None
    #: The answer text already on screen, exactly as streamed.
    streamed: str = ""
    sealed: bool = False
    updated_at: float = 0.0

    @property
    def handle(self) -> ProgressHandle:
        return ProgressHandle(
            kind=self.kind,
            message_id=self.message_id,
            channel_id=self.channel_id,
            streamed_text=bool(self.streamed),
        )


def _store() -> RedisJsonCache:
    return surface_caches.get(LIVE_DISPLAYS)


async def record_live_display(
    run_id: UUID,
    *,
    conversation_id: UUID,
    handle: ProgressHandle,
    streamed: str,
    sealed: bool = False,
) -> None:
    """Remember (or update) the run's stream. Never raises."""
    display = LiveDisplay(
        conversation_id=conversation_id,
        kind=handle.kind,
        message_id=handle.message_id,
        channel_id=handle.channel_id,
        streamed=streamed,
        sealed=sealed,
        updated_at=time.time(),
    )
    try:
        await _store().set_json(str(run_id), display.model_dump(mode="json"))
    except _STORE_ERRORS:
        logger.warning(
            "agent_surfaces.live_display.not_recorded.degraded",
            run_id=str(run_id),
            exc_info=True,
        )


async def forget_live_display(run_id: UUID) -> None:
    """The stream is closed or gone: nothing is left to finish. Never raises."""
    try:
        await _store().delete(str(run_id))
    except _STORE_ERRORS:
        logger.warning(
            "agent_surfaces.live_display.not_forgotten.degraded",
            run_id=str(run_id),
            exc_info=True,
        )


async def _read(run_id: UUID) -> LiveDisplay | None:
    try:
        raw = await _store().get_json(str(run_id))
    except _STORE_ERRORS:
        logger.warning(
            "agent_surfaces.live_display.not_read.degraded",
            run_id=str(run_id),
            exc_info=True,
        )
        return None
    except ValueError:
        return None
    if raw is None:
        return None
    try:
        return LiveDisplay.model_validate(raw)
    except ValidationError:
        return None


async def take_live_display(
    run_id: UUID, *, wait_seconds: float | None = None
) -> LiveDisplay | None:
    """The run's stream, once its observer has let go of it (or given up waiting)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + (
        SEAL_WAIT_SECONDS if wait_seconds is None else wait_seconds
    )
    display = await _read(run_id)
    while display is not None and not display.sealed and loop.time() < deadline:
        await asyncio.sleep(_SEAL_POLL_SECONDS)
        display = await _read(run_id)
    if display is not None and not display.sealed:
        logger.info(
            "agent_surfaces.live_display.unsealed.observed",
            run_id=str(run_id),
        )
    return display


def remainder_after(streamed: str, answer: str) -> str | None:
    """What of ``answer`` the stream does not show yet; None if they disagree.

    Whitespace at either end of the stream is not a disagreement: the stream
    carries the model's raw deltas, the stored answer is trimmed.
    """
    shown = streamed.strip()
    full = answer.strip()
    if not shown:
        return full
    if not full.startswith(shown):
        return None
    rest = full[len(shown) :]
    trailing = streamed[len(streamed.rstrip()) :]
    if trailing and rest.startswith(trailing):
        rest = rest[len(trailing) :]
    return rest


__all__ = [
    "SEAL_WAIT_SECONDS",
    "LiveDisplay",
    "forget_live_display",
    "record_live_display",
    "remainder_after",
    "take_live_display",
]
