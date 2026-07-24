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
                "error_fingerprint",
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
    "http.request.completed": EventSpec(
        "debug", frozenset({"duration_ms", "method", "route", "status_code"})
    ),
    "http.request.slow": EventSpec(
        "warning",
        frozenset({"duration_ms", "latency_kind", "method", "route", "status_code"}),
    ),
    "agentbox.operation.completed": EventSpec(
        "info",
        frozenset(
            {
                "duration_ms",
                "operation",
                "outcome",
                "profile",
                "provider",
                "workload_kind",
            }
        ),
    ),
    "agentbox.operation.failed": EventSpec(
        "error",
        frozenset(
            {
                "duration_ms",
                "error_code",
                "error_fingerprint",
                "error_type",
                "operation",
                "outcome",
                "profile",
                "provider",
                "workload_kind",
            }
        ),
    ),
    "agentbox.operation.timed_out": EventSpec(
        "error",
        frozenset(
            {
                "duration_ms",
                "error_code",
                "error_fingerprint",
                "error_type",
                "operation",
                "outcome",
                "profile",
                "provider",
                "workload_kind",
            }
        ),
    ),
    "agentbox.operation.cancelled": EventSpec(
        "warning",
        frozenset(
            {
                "duration_ms",
                "error_code",
                "error_fingerprint",
                "error_type",
                "operation",
                "outcome",
                "profile",
                "provider",
                "workload_kind",
            }
        ),
    ),
    "agentbox.operation.rejected": EventSpec(
        "warning",
        frozenset(
            {
                "duration_ms",
                "error_code",
                "error_fingerprint",
                "error_type",
                "operation",
                "outcome",
                "profile",
                "provider",
                "workload_kind",
            }
        ),
    ),
    "agentbox.cleanup.completed": EventSpec(
        "debug", frozenset({"count", "duration_ms", "outcome"})
    ),
    "agentbox.cleanup.failed": EventSpec(
        "warning",
        frozenset({"count", "duration_ms", "error_type", "outcome"}),
    ),
    "agentbox.reconcile.completed": EventSpec(
        "debug", frozenset({"count", "duration_ms", "outcome"})
    ),
    "agentbox.reconcile.failed": EventSpec(
        "warning",
        frozenset({"count", "duration_ms", "error_type", "outcome"}),
    ),
    "port.proxy.upstream_failed": EventSpec(
        "error",
        frozenset({"method", "protocol", "provider"}),
    ),
    "release.identity.malformed": EventSpec("warning", frozenset()),
    "release.identity.missing": EventSpec("warning", frozenset()),
}
