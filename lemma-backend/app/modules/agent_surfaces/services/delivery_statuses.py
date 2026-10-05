"""Making a platform's report that a message never arrived visible.

WhatsApp answers a send with a message id and reports failure later, by
webhook. Before this, those webhooks parsed as "not a message" and were
dropped without a trace.

They are logged, not acted on. Acting on one means knowing what was sent and to
which conversation, and the report names neither -- only Meta's message id and
the recipient's number. Keeping a log of every send to answer that was more
machinery than the failures warrant: the commonest one, a closed 24-hour reply
window, is now refused before the send (``reply_window_fallback``), so what is
left reaching here is rare, and what it needs is to be seen.
"""

from __future__ import annotations

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import platform_value_for_source
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)

logger = get_logger(__name__)


def log_delivery_statuses(
    payload: dict[str, object],
    *,
    source: str,
    adapters: SurfacePlatformAdapterRegistry | None = None,
) -> int:
    """Log every failure this webhook reports. Returns how many there were.

    The recipient's number is left out on purpose: the code and Meta's title say
    what went wrong, and a phone number in a log line is a phone number in every
    place the logs are shipped to.
    """
    platform = platform_value_for_source(source)
    adapter = (adapters or SurfacePlatformAdapterRegistry()).get(platform or "")
    if adapter is None or platform is None:
        return 0
    failures = adapter.parse_delivery_statuses(payload)
    for failure in failures:
        logger.warning(
            "agent_surfaces.delivery_statuses.send_failed.degraded",
            platform=platform,
            failure_code=failure.code,
            failure_title=failure.title,
        )
    return len(failures)


__all__ = ["log_delivery_statuses"]
