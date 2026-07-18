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
    'agentbox.cleanup.failed': EventSpec('error', frozenset({'sandbox_id'})),
    'agentbox.daytona.capacity_exhausted': EventSpec('warning', frozenset({'capacity_limit', 'observed_active_count', 'sandbox_id'})),
    'agentbox.e2b.capacity_exhausted': EventSpec('warning', frozenset({'capacity_limit', 'observed_active_count', 'sandbox_id'})),
    'agentbox.e2b.discard_unbootstrapped_e2b_sandbox_s.propagated': EventSpec('debug', frozenset({'sandbox_id'})),
    'agentbox.e2b.e2b_endpoint_refresh_control_plane.diagnostic': EventSpec('debug', frozenset({'instance_id', 'sandbox_id'})),
    'agentbox.e2b.e2b_kill_already_absent_reason.observed': EventSpec('debug', frozenset({'provider_id', 'sandbox_id'})),
    'agentbox.e2b.e2b_kill_requested_reason_create.diagnostic': EventSpec('debug', frozenset({'provider_id', 'sandbox_id'})),
    'agentbox.e2b.e2b_kill_requested_reason_logical.diagnostic': EventSpec('debug', frozenset({'provider_id', 'sandbox_id'})),
    'agentbox.e2b.e2b_kill_requested_reason_orphan.diagnostic': EventSpec('debug', frozenset({'provider_id', 'sandbox_id'})),
    'agentbox.e2b.e2b_status_reconnected_sandbox_id.observed': EventSpec('debug', frozenset({'provider_id', 'sandbox_id'})),
    'agentbox.e2b.timeout_renewal_deferred.diagnostic': EventSpec('debug', frozenset({'sandbox_id'})),
    'agentbox.endpoint_transport.endpoint_gateway_was_temporarily_unroutable.diagnostic': EventSpec('debug', frozenset({'attempt'})),
    'agentbox.kubernetes.recreating_sandbox_s_existing_pod.diagnostic': EventSpec('debug', frozenset({'sandbox_id'})),
    'agentbox.kubernetes.runtime_returned_http_s.diagnostic': EventSpec('debug', frozenset({'runtime_status'})),
    'agentbox.kubernetes.runtime_returned_malformed_json.diagnostic': EventSpec('debug', frozenset()),
    'agentbox.lease.lost': EventSpec('error', frozenset({'lease_id'})),
    'agentbox.lifecycle.claim_lost': EventSpec('error', frozenset({'operation', 'sandbox_id'})),
    'agentbox.lifecycle_manager.background_provider_lease_renewal_sandbox.diagnostic': EventSpec('debug', frozenset({'sandbox_id'})),
    'agentbox.lifecycle_manager.reconciliation_deferred_retryable_provider_outcome.diagnostic': EventSpec('debug', frozenset({'error_code', 'sandbox_id'})),
    'agentbox.lifecycle_manager.reconciliation_skipped_busy_lifecycle_sandbox.observed': EventSpec('debug', frozenset({'sandbox_id'})),
    'agentbox.route_repair.failed': EventSpec('error', frozenset({'sandbox_id'})),
    'agentbox.runtime.request_failed': EventSpec('error', frozenset()),
    'agentbox.runtime_proxy.runtime_s_returned_http_s.diagnostic': EventSpec('debug', frozenset({'operation', 'status_code'})),
    'agentbox.runtime_proxy.runtime_s_returned_invalid_response.diagnostic': EventSpec('debug', frozenset({'operation'})),
    'agentbox.runtime_proxy.runtime_s_returned_malformed_json.diagnostic': EventSpec('debug', frozenset({'operation'})),
    'agentbox.runtime_proxy.runtime_s_returned_non_object.diagnostic': EventSpec('debug', frozenset({'operation'})),
    'agentbox.runtime_proxy.runtime_s_transport_s_s.diagnostic': EventSpec('debug', frozenset({'operation'})),
    'dependency.degraded': EventSpec('warning', frozenset({'dependency', 'error_type', 'failure_count', 'incident_duration_ms'})),
    'dependency.recovered': EventSpec('info', frozenset({'dependency', 'failure_count', 'incident_duration_ms'})),
    'http.request.completed': EventSpec('debug', frozenset({'duration_ms', 'method', 'route', 'status_code'})),
    'http.request.failed': EventSpec('error', frozenset({'duration_ms', 'error_code', 'error_type', 'method', 'route', 'status_code'})),
    'http.request.rate_limited': EventSpec('warning', frozenset({'duration_ms', 'method', 'route', 'status_code'})),
    'http.request.slow': EventSpec('warning', frozenset({'duration_ms', 'latency_kind', 'method', 'route', 'status_code'})),
    'release.identity.malformed': EventSpec('warning', frozenset()),
    'release.identity.missing': EventSpec('warning', frozenset()),
    'service.started': EventSpec('info', frozenset()),
    'service.stopped': EventSpec('info', frozenset()),
}
