"""Generated exact event contract for this independently deployed service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class EventSpec:
    level: Literal["debug", "info", "warning", "error"]
    fields: frozenset[str] = frozenset()


EVENT_CATALOG: dict[str, EventSpec] = {
    "logging.contract.violation": EventSpec("error"),
    "dependency.degraded": EventSpec(
        "warning",
        frozenset(
            {"dependency", "error_type", "failure_count", "incident_duration_ms"}
        ),
    ),
    "dependency.recovered": EventSpec(
        "info", frozenset({"dependency", "failure_count", "incident_duration_ms"})
    ),
    "http.request.failed": EventSpec(
        "error",
        frozenset(
            {
                "duration_ms",
                "error_code",
                "error_type",
                "method",
                "route",
                "status_code",
            }
        ),
    ),
    "http.request.rate_limited": EventSpec(
        "warning", frozenset({"duration_ms", "method", "route", "status_code"})
    ),
    "release.identity.malformed": EventSpec("warning", frozenset()),
    "release.identity.missing": EventSpec("warning", frozenset()),
}
