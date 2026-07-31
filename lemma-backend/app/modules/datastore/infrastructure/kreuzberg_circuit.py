"""In-process circuit breaker for the Kreuzberg extractor.

When Kreuzberg is down (e.g. a scale-to-zero container that isn't warming), every
extraction otherwise burns its full connect + retry budget before failing. With
document processing running concurrently and a recovery cron that keeps
re-driving stale files, that pins worker slots and memory on a known-dead
dependency. This breaker short-circuits repeated connection failures: after
``failure_threshold`` consecutive failures it opens, and while open extractions
fail fast (raising ``KreuzbergCircuitOpen``) instead of waiting on the network.
After ``reset_seconds`` it half-opens to let a single trial through; a success
closes it, a failure re-opens it.

The worker is a single process, so a plain in-memory breaker (no Redis) is
sufficient and matches the module-scope-singleton shape used elsewhere in the
datastore infra (e.g. ``pdf_page_rendering._render_semaphore``). It is
best-effort under concurrency (a couple of trial requests may slip through while
half-open); that is fine — the goal is to stop the sustained pile-up, not to
gate every request exactly.
"""

from __future__ import annotations

import time
from typing import Callable

from app.core.log.log import get_logger
from app.modules.datastore.config import datastore_settings

logger = get_logger(__name__)


class KreuzbergCircuitOpen(RuntimeError):
    """Raised when the Kreuzberg circuit is open (extractor treated as down)."""


class KreuzbergCircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int,
        reset_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._failure_threshold = max(1, failure_threshold)
        self._reset_seconds = reset_seconds
        self._clock = clock
        self._consecutive_failures = 0
        # Monotonic timestamp the circuit opened, or None while closed.
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None

    def raise_if_open(self) -> None:
        """Fail fast if the circuit is open and still within its cooldown.

        Once the cooldown elapses the circuit is left ``open`` but this permits a
        single trial request through (half-open); the caller's subsequent
        ``record_success``/``record_failure`` closes or re-opens it.
        """
        if self._opened_at is None:
            return
        if (self._clock() - self._opened_at) < self._reset_seconds:
            raise KreuzbergCircuitOpen(
                "Kreuzberg circuit open; skipping extraction (extractor appears down)"
            )
        # Cooldown elapsed → allow a half-open trial; state stays open until the
        # trial's outcome is recorded.

    def record_success(self) -> None:
        failure_count = self._consecutive_failures
        opened_at = self._opened_at
        self._consecutive_failures = 0
        self._opened_at = None
        if opened_at is not None:
            logger.info(
                "dependency.recovered",
                dependency="kreuzberg",
                failure_count=failure_count,
                incident_duration_ms=round((self._clock() - opened_at) * 1000, 1),
            )

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._opened_at is not None:
            # Failure during a half-open trial → re-arm the cooldown.
            self._opened_at = self._clock()
            return
        if self._consecutive_failures >= self._failure_threshold:
            self._opened_at = self._clock()
            logger.warning(
                "dependency.degraded",
                dependency="kreuzberg",
                error_type="KreuzbergFailure",
                failure_count=self._consecutive_failures,
                incident_duration_ms=0.0,
            )


_circuit: KreuzbergCircuitBreaker | None = None


def get_kreuzberg_circuit() -> KreuzbergCircuitBreaker:
    global _circuit
    if _circuit is None:
        _circuit = KreuzbergCircuitBreaker(
            failure_threshold=datastore_settings.kreuzberg_circuit_failure_threshold,
            reset_seconds=datastore_settings.kreuzberg_circuit_reset_seconds,
        )
    return _circuit


def reset_kreuzberg_circuit() -> None:
    """Test hook: drop the process-wide breaker so it rebuilds from settings."""
    global _circuit
    _circuit = None
