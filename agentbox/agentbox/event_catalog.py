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
    "http.request.completed": EventSpec(
        "debug", frozenset({"duration_ms", "method", "route", "status_code"})
    ),
    "http.request.slow": EventSpec(
        "warning",
        frozenset({"duration_ms", "latency_kind", "method", "route", "status_code"}),
    ),
    "agentbox.cleanup.failed": EventSpec(
        "warning",
        frozenset({"error_type"}),
    ),
    "agentbox.reconcile.failed": EventSpec(
        "warning",
        frozenset({"error_type"}),
    ),
    "agentbox.admission.invariant_repaired": EventSpec(
        "warning",
        frozenset({"allocation_count", "provider_scope"}),
    ),
    "port.proxy.upstream_failed": EventSpec(
        "error",
        frozenset({"method", "protocol", "provider"}),
    ),
    "release.identity.malformed": EventSpec("warning", frozenset()),
    "release.identity.missing": EventSpec("warning", frozenset()),
}
