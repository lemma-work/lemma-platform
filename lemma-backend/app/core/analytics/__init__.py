"""Product analytics: who did what, through what, and did the pod deliver.

Distinct from :mod:`app.core.observability`, which answers whether the system
is broken or slow. That plane's export boundary is default-deny by design and
strips exactly the business context product questions need, so the two cannot
be merged -- see ``docs/design/product-analytics.md``.

Nothing here is a source of truth. Billing reads Postgres.
"""

from app.core.analytics.emitter import AnalyticsActor, configure, current_sink, emit
from app.core.analytics.event_catalog import ANALYTICS_CATALOG, AnalyticEvent
from app.core.analytics.sink import AnalyticsSink, CapturedEvent, MemorySink, NullSink

__all__ = [
    "ANALYTICS_CATALOG",
    "AnalyticEvent",
    "AnalyticsActor",
    "AnalyticsSink",
    "CapturedEvent",
    "MemorySink",
    "NullSink",
    "configure",
    "current_sink",
    "emit",
]
