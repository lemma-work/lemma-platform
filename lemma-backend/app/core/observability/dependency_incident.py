"""Bounded transition logging for dependencies that can fail repeatedly."""

from __future__ import annotations

import time

from app.core.log.log import Logger


class DependencyIncident:
    """Emit one degraded/recovered pair instead of one record per attempt."""

    def __init__(
        self,
        dependency: str,
        *,
        logger: Logger,
        degradation_threshold: int = 3,
    ) -> None:
        self._dependency = dependency
        self._logger = logger
        self._threshold = max(degradation_threshold, 1)
        self._failure_count = 0
        self._started_at: float | None = None
        self._degraded = False

    def record_failure(self, *, error_type: str) -> None:
        now = time.monotonic()
        if self._started_at is None:
            self._started_at = now
        self._failure_count += 1
        if self._degraded or self._failure_count < self._threshold:
            return
        self._degraded = True
        self._logger.warning(
            "dependency.degraded",
            dependency=self._dependency,
            error_type=error_type,
            failure_count=self._failure_count,
            incident_duration_ms=round((now - self._started_at) * 1000, 1),
        )

    def record_success(self) -> None:
        if self._started_at is None:
            return
        if self._degraded:
            self._logger.info(
                "dependency.recovered",
                dependency=self._dependency,
                failure_count=self._failure_count,
                incident_duration_ms=round(
                    (time.monotonic() - self._started_at) * 1000, 1
                ),
            )
        self._failure_count = 0
        self._started_at = None
        self._degraded = False
