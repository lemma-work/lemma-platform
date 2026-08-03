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
    "agentbox.process_lease.failed": EventSpec("warning", frozenset()),
    "agentbox.inventory.sweep_failed": EventSpec("warning", frozenset()),
    "agentbox.inventory.listing_failed": EventSpec(
        "warning",
        frozenset({"error_type", "provider_scope"}),
    ),
    "agentbox.inventory.unrecognised_sandbox_ignored": EventSpec(
        "info",
        frozenset({"provider_id", "provider_scope"}),
    ),
    "agentbox.inventory.provider_paused_active_allocation": EventSpec(
        "warning",
        frozenset({"provider_id", "provider_scope", "workload_kind"}),
    ),
    "agentbox.inventory.untracked_sandbox_destroyed": EventSpec(
        "warning",
        frozenset({"allocation_state", "provider_id", "provider_scope"}),
    ),
    "agentbox.inventory.untracked_destroy_failed": EventSpec(
        "warning",
        frozenset({"error_type", "provider_id", "provider_scope"}),
    ),
    "port.proxy.upstream_failed": EventSpec(
        "error",
        frozenset({"method", "protocol", "provider"}),
    ),
    "release.identity.malformed": EventSpec("warning", frozenset()),
    "release.identity.missing": EventSpec("warning", frozenset()),
}
