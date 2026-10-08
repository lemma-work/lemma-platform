"""What the service needs from a provider and from a rate limiter."""

from __future__ import annotations

from typing import Protocol

from app.modules.decisions.domain.answers import DecisionResult
from app.modules.decisions.domain.request import DecisionCaller, DecisionTask


class DecisionProvider(Protocol):
    """Answers a checked task, every question kind included.

    Raises `DecisionUnavailableError` when it cannot answer at all. Never
    answers outside the questions: the service checks, and treats a provider
    that does as unavailable.
    """

    @property
    def name(self) -> str: ...

    async def decide(
        self, task: DecisionTask, *, caller: DecisionCaller, timeout_seconds: float
    ) -> DecisionResult: ...


class DecisionRateLimiter(Protocol):
    async def retry_after(self, key: str) -> int | None:
        """Seconds to wait if `key` is over its limit, else `None`."""
        ...
