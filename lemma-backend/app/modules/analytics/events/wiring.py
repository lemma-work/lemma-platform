"""What every source consumer in this package shares.

Named `wiring` rather than `router` so that `events.router` can only ever
mean the object, never this module: the package binds both names, and a
submodule shadowing its own export is a bug that reads as a typo.

One router, so the module registers a single object; one consumer group per
stream, named for analytics so its cursor is independent of the consumers the
product depends on.

Why the bus and not the controllers:

* instrumentation cannot drift from behaviour, because there is nothing to keep
  in sync -- no controller mentions analytics;
* an event that fires is an event that committed, since the outbox writes in
  the same transaction as the state change. Controller-level emits routinely
  report actions that later rolled back;
* every origin is covered by one implementation. A pod created from the CLI,
  from an agent over MCP, from a coding agent over ACP or from a workflow node
  all land on the same domain event.

``origin`` rides on the event itself (``DomainEvent.origin``), captured where
the work arrived. These consumers never infer it from their own surroundings --
they run in a worker, where those surroundings say nothing about the caller.
"""

from __future__ import annotations

from uuid import UUID

from faststream.redis import RedisRouter

from app.core.analytics import AnalyticsActor
from app.core.authorization.context import ActorType
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.origin import origin_from_payload

router = RedisRouter()

#: Rebuild the origin the work arrived through, from the event itself. Shared
#: with the inbox, which binds the same value as a contextvar so handlers that
#: raise their own events inherit it.
origin_of = origin_from_payload

#: Terminal statuses that count as an outcome. A failed or cancelled run is not
#: a delivery, however it arrived.
DELIVERED_STATUSES = frozenset({"COMPLETED", "SUCCEEDED"})


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


def actor_or_system(actor_id: UUID | None) -> AnalyticsActor:
    """The person who did it, or the platform when the event names nobody."""
    if actor_id is None:
        return AnalyticsActor.autonomous(ActorType.SYSTEM)
    return AnalyticsActor.user(actor_id)


__all__ = [
    "DELIVERED_STATUSES",
    "actor_or_system",
    "origin_of",
    "provide_uow_factory",
    "router",
]
