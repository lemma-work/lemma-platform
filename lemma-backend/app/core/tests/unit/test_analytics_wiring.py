"""Hold the gap between the designed catalog and what is actually emitted.

A catalog entry nothing raises is a dashboard that is permanently zero, and the
worst version of that is the one nobody knows about. This test names the
unwired events explicitly, so wiring one -- or deciding not to -- is a visible
edit rather than a silent drift.
"""

from __future__ import annotations

from app.composition.analytics_consumer import WIRED_EVENTS
from app.core.analytics.event_catalog import ANALYTICS_CATALOG


#: Emitted by the web client in Phase 2, not by the backend: these are steps a
#: person takes before any API call, so the server never sees them.
CLIENT_EMITTED = frozenset({"share_link.viewed", "import.started"})

#: Designed, not yet raised by anything. Each needs a domain event the platform
#: does not currently publish, or a lookup the consumer does not yet do.
#: Shrinking this set is the Phase 1 follow-up.
KNOWN_GAPS = frozenset(
    {
        "organization.created",
        "organization.member_joined",
        "pod.delivered",
        "agent.created",
        "workflow.created",
        "schedule.created",
        "app.created",
        "app.published",
        "app.session_started",
        "conversation.started",
        "workflow_run.completed",
        "schedule_run.completed",
        "surface.connected",
        "surface.message_answered",
        "connector.connected",
        "connector.operation_executed",
        "bundle.exported",
        "import.completed",
    }
)


def test_every_catalog_event_is_wired_client_emitted_or_a_named_gap() -> None:
    unaccounted = set(ANALYTICS_CATALOG) - WIRED_EVENTS - CLIENT_EMITTED - KNOWN_GAPS
    assert not unaccounted, (
        "catalog events with no emitter and no entry in KNOWN_GAPS: "
        f"{sorted(unaccounted)}"
    )


def test_the_gap_lists_do_not_claim_events_that_left_the_catalog() -> None:
    stale = (KNOWN_GAPS | CLIENT_EMITTED) - set(ANALYTICS_CATALOG)
    assert not stale, f"listed events no longer in the catalog: {sorted(stale)}"


def test_wired_events_are_all_real_catalog_entries() -> None:
    unknown = WIRED_EVENTS - set(ANALYTICS_CATALOG)
    assert not unknown, f"consumer claims events absent from the catalog: {sorted(unknown)}"
