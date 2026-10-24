"""What the compute meter has already said, so it says each thing once.

Kept in Redis: a warning at 80% is one notice per window, not one per
five-minute tick, and "out of compute since" is a single timestamp per
machine that the grace period is measured from.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.config import settings
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger

if TYPE_CHECKING:
    from app.modules.workspace.services.compute_meter import ComputeVerdict

logger = get_logger(__name__)

_WARNED = "machine:compute:warned:v1:{subject}:{window}"
_EXHAUSTED = "machine:compute:exhausted:v1:{sandbox_id}"
_LATEST = "machine:compute:latest:v1:{subject}"
_THRESHOLDS = (0.8, 0.95)
_TTL_SECONDS = 40 * 24 * 3600


class ComputeNotices:
    def __init__(self) -> None:
        self._redis = get_redis(url=settings.redis_url)

    async def warn_if_crossed(
        self, subject: str, verdict: ComputeVerdict, *, sandbox_id: UUID
    ) -> float | None:
        """The highest threshold newly crossed, logged once per window."""
        if verdict.fraction is None:
            return None
        window = verdict.resets_at.strftime("%Y-%m")
        crossed = [t for t in _THRESHOLDS if verdict.fraction >= t]
        if not crossed:
            return None
        threshold = crossed[-1]
        key = _WARNED.format(subject=subject, window=window)
        previous = await self._redis.get(key)
        if previous is not None and float(previous) >= threshold:
            return None
        await self._redis.set(key, str(threshold), ex=_TTL_SECONDS)
        await self._redis.set(
            _LATEST.format(subject=subject), f"{threshold}|{window}", ex=_TTL_SECONDS
        )
        logger.info(
            "workspace.compute_meter.threshold_crossed.observed",
            sandbox_id=str(sandbox_id),
            threshold=threshold,
        )
        return threshold

    async def latest_warning(self, subject: str, *, window: str) -> float | None:
        raw = await self._redis.get(_LATEST.format(subject=subject))
        if raw is None:
            return None
        value = raw if isinstance(raw, str) else bytes(raw).decode()
        threshold, _, warned_window = value.partition("|")
        if warned_window != window:
            return None
        try:
            return float(threshold)
        except ValueError:
            return None

    async def exhausted_since(self, sandbox_id: UUID, now: datetime) -> datetime:
        key = _EXHAUSTED.format(sandbox_id=sandbox_id)
        await self._redis.set(key, now.isoformat(), nx=True, ex=_TTL_SECONDS)
        raw = await self._redis.get(key)
        value = raw if isinstance(raw, str) else bytes(raw or b"").decode()
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return now.astimezone(timezone.utc)

    async def clear_exhausted(self, sandbox_id: UUID) -> None:
        await self._redis.delete(_EXHAUSTED.format(sandbox_id=sandbox_id))
