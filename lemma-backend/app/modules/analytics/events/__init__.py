"""Every source stream analytics reads, and the one router they register on.

One file per source, because the streams have nothing to do with each other: an
identity consumer and a workflow consumer share a vocabulary, not a reason to
change. Importing them here is what registers their subscriptions, so this list
is the module's wiring -- a source missing from it is a stream nobody reads.
"""

from __future__ import annotations

from app.modules.analytics.events import (
    agent,
    apps,
    connectors,
    datastore,
    function,
    identity,
    pod,
    pod_bundle,
    schedule,
    surfaces,
    workflow,
)
from app.modules.analytics.events.wiring import router

#: Catalog events raised somewhere other than a consumer in this package. Each
#: is on a request path, where the fact is known and the bus is not involved:
#: `pod.delivered` from this module's own delivery service, `app.session_started`
#: from `app/core/analytics/app_session.py`, and `connector.operation_executed`
#: from `connectors/domain/analytics.py`. Listed rather than inferred, because
#: the point of `WIRED_EVENTS` is that nothing goes unaccounted for.
_RAISED_OUTSIDE_THIS_PACKAGE = frozenset(
    {"pod.delivered", "app.session_started", "connector.operation_executed"}
)

#: Catalog events the backend actually raises today. The catalog is the designed
#: contract; this is reality, and ``test_analytics_wiring.py`` holds the
#: difference visible so an unwired event is a tracked gap rather than a
#: dashboard that is quietly always zero.
#:
#: Assembled from each source file's own claim rather than restated in one list,
#: so that deleting an emit and forgetting this set is not a thing that can
#: happen from two directions.
WIRED_EVENTS = frozenset().union(
    _RAISED_OUTSIDE_THIS_PACKAGE,
    agent.WIRED,
    apps.WIRED,
    connectors.WIRED,
    datastore.WIRED,
    function.WIRED,
    identity.WIRED,
    pod.WIRED,
    pod_bundle.WIRED,
    schedule.WIRED,
    surfaces.WIRED,
    workflow.WIRED,
)

__all__ = ["WIRED_EVENTS", "router"]
