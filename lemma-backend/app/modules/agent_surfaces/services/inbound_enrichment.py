"""Filling in what a webhook did not carry, and knowing when to stop trying.

Split out of ``ingress_service`` because it needs none of that service's state —
and because the file is already the largest in the module, so the branch that
decides whether a delivery is worth retrying should not have to live in it.
"""

from __future__ import annotations

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.platforms.common import (
    UNRETRYABLE_PROVIDER_STATUS,
    provider_failure,
)

logger = get_logger(__name__)


async def enrich_or_drop(
    *,
    adapter,
    surface: AgentSurfaceEntity,
    parsed: ParsedInboundSurfaceEvent,
    credentials: dict,
) -> ParsedInboundSurfaceEvent | None:
    """The enriched event, or None when this delivery is over.

    Failures are **not** swallowed. For a provider whose webhook carries no
    body, enrichment *is* the message, so continuing would drop the email
    permanently — the webhook already returned 200. Raising is what makes the
    delivery retry.

    Except when the answer will not change. A 401 or 403 is the provider
    refusing this credential, and no number of retries turns a send-only API
    key into one that may read inbound mail; it only spends the queue and
    buries the cause under repeats of itself.

    The status and the provider's own error name are logged because the
    pipeline strips ``error`` — without them this reached production as
    "enrichment failed" and nothing more, and the cause took a database and
    the provider's own API to recover.
    """
    try:
        enriched = await adapter.enrich_inbound_event(
            credentials=credentials,
            event=parsed,
        )
    except Exception as exc:
        failure = provider_failure(exc)
        logger.warning(
            "agent_surfaces.ingress_service.inbound_enrichment_failed.degraded",
            surface_type=surface.surface_type,
            failure_type=failure.failure_type,
            status_code=failure.status_code,
            provider_error=failure.provider_error,
        )
        if failure.status_code in UNRETRYABLE_PROVIDER_STATUS:
            return None
        raise

    if enriched is None:
        logger.debug(
            "agent_surfaces.ingress_service.agent_surface_dropped_event_after.observed",
            surface_type=surface.surface_type,
        )
    return enriched


__all__ = ["enrich_or_drop"]
