"""Transport-agnostic outbound delivery retry policy for agent surfaces.

Platform services build their own payloads against their own transports (httpx,
slack_sdk, aiohttp), so the shared piece is *retry + classification*, not an
HTTP client. ``with_retry`` runs a send callable, asks a platform-supplied
``classify`` whether a failure is transient, and retries transient failures
with bounded exponential backoff (honoring a server-provided retry-after when
available).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import TypeVar

from app.modules.agent_surfaces.domain.delivery_limits import MAX_DELIVERY_ATTEMPTS

T = TypeVar("T")


class DeliveryClassification(Enum):
    """Whether a failed delivery attempt is worth retrying."""

    TRANSIENT = "transient"
    PERMANENT = "permanent"


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = MAX_DELIVERY_ATTEMPTS
    base_delay: float = 0.5
    max_delay: float = 8.0

    def backoff(self, attempt: int, *, retry_after: float | None = None) -> float:
        """Delay before the ``attempt``-th retry (1-based).

        A positive ``retry_after`` (e.g. Telegram 429 ``parameters.retry_after``)
        wins over exponential backoff, capped at ``max_delay``.
        """
        if retry_after is not None and retry_after > 0:
            return min(float(retry_after), self.max_delay)
        return min(self.base_delay * (2 ** max(attempt - 1, 0)), self.max_delay)


async def with_retry(
    send: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    classify: Callable[[Exception], DeliveryClassification],
    retry_after: Callable[[Exception], float | None] | None = None,
    on_retry: Callable[[int, Exception], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run ``send`` with bounded retry on transient failures.

    ``classify`` decides transient-vs-permanent per exception. Permanent
    failures (and the final attempt) re-raise. ``sleep`` is injectable for tests.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return await send()
        except Exception as exc:  # noqa: BLE001 - re-raised after classification
            if (
                classify(exc) is DeliveryClassification.PERMANENT
                or attempt >= policy.max_attempts
            ):
                raise
            delay = policy.backoff(
                attempt,
                retry_after=retry_after(exc) if retry_after is not None else None,
            )
            if on_retry is not None:
                on_retry(attempt, exc)
            await sleep(delay)
