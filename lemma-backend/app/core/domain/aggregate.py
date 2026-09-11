"""Aggregate Root base class for DDD."""

from typing import TYPE_CHECKING

from pydantic import PrivateAttr

from app.core.domain.entity import Entity

if TYPE_CHECKING:
    from app.core.domain.events import DomainEvent


class AggregateRoot(Entity):
    """Base class for aggregate roots that collect domain events.

    Aggregates are the consistency boundary for domain operations.
    Events are collected during domain operations and published on commit.
    """

    # `default=[]` rather than `default_factory=list`, and the empty list is not
    # shared: pydantic copies a private attribute's default into every instance
    # (`smart_deepcopy`), so each aggregate still gets its own list.
    #
    # The spelling is load-bearing. `default_factory` sends every single
    # instantiation through `takes_validated_data_argument`, which calls
    # `inspect.signature(list)` to decide whether the factory wants the
    # validated data -- uncached, twice per instance, and `list` is a C builtin,
    # which is the slowest thing to introspect. That is 63us per entity against
    # 2.6us for this line: a 24x tax on every domain object the process builds.
    # It stalled the API event loop for up to 1.7s listing one pod's files, and
    # the stall sampler named this frame in 38 of the 55 reports on the release
    # it was measured on. (Across the whole preceding week it is 8%: that window
    # is dominated by a decode path fixed in #618 but not yet deployed. The
    # sampler reports a stall once it crosses a one-second threshold, so its
    # counts compare sites against each other -- they are not a measure of total
    # blocked time.)
    #
    # `scripts/check_io_hygiene.py` fails the build if this comes back.
    _domain_events: list["DomainEvent"] = PrivateAttr(default=[])

    def add_event(self, event: "DomainEvent") -> None:
        """Register a domain event to be published on commit."""
        self._domain_events.append(event)

    def collect_events(self) -> list["DomainEvent"]:
        """Collect and clear pending domain events.

        Called by repository on save to gather events for publishing.
        """
        events = self._domain_events.copy()
        self._domain_events.clear()
        return events

    def has_pending_events(self) -> bool:
        """Check if there are pending events."""
        return len(self._domain_events) > 0
