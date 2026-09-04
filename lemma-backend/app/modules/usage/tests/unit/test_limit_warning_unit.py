"""Warning somebody once, at the moment it becomes worth warning them.

The only signal a spend limit ever gave was the 429 that refused work, by which
time the useful moment has passed. What an operator can act on is "you are at
80% with three weeks of the month left".

Two properties decide whether that is a warning or noise. It has to fire on the
*crossing*, because somebody over the line starts every subsequent conversation
over the line -- warn while above and the one that mattered is indistinguishable
from the hundred that followed. And it must not fire where there is nothing to
approach: an unlimited window, or one that is already refusing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.modules.usage.domain.events import UsageLimitApproachingEvent
from app.modules.usage.services.limit_windows import limit_scope
from app.modules.usage.services.reservation_sizing import approaching_events

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)


def _scope(
    *, limit: float | None, used: float, reserved: float = 0.0
) -> dict[str, object]:
    return limit_scope(
        limit_usd=limit,
        used_usd=used,
        reserved_usd=reserved,
        reset_at=_NOW + timedelta(days=26),
        window_start_at=_NOW - timedelta(days=4),
        scope="organization",
        counter_organization_id=uuid4(),
        warn_fraction=0.8,
    )


def _event(
    scope: dict[str, object], *, reserved: float, fraction: float = 0.8
) -> UsageLimitApproachingEvent | None:
    """The one warning this reservation would raise, or None.

    Driven through the public entry point rather than the per-scope helper, so
    these tests exercise the loop the service actually calls.
    """
    events = approaching_events(
        {"org_monthly": scope},
        reserved=reserved,
        fraction=fraction,
        user_id=uuid4(),
    )
    assert len(events) <= 1
    return events[0] if events else None


def test_the_hold_that_carries_a_window_over_the_line_warns():
    # $7.90 spent of $10, and a hold of $0.20 takes it past $8.
    crossing = _scope(limit=10.0, used=7.9)

    event = _event(crossing, reserved=0.2)

    assert event is not None
    assert event.limit_usd == 10.0
    assert event.consumed_usd == pytest.approx(8.1)
    assert event.threshold_fraction == 0.8
    # The window, not only the fraction: 80% means nothing without saying 80% of
    # what, and until when.
    assert event.reset_at == _NOW + timedelta(days=26)


def test_a_window_already_over_the_line_does_not_warn_again():
    """The property that makes this one email rather than one per run."""
    already_over = _scope(limit=10.0, used=8.5)

    assert _event(already_over, reserved=0.2) is None


def test_a_hold_that_does_not_reach_the_line_says_nothing():
    well_under = _scope(limit=10.0, used=1.0)

    assert _event(well_under, reserved=0.2) is None


def test_an_unlimited_window_has_nothing_to_approach():
    unlimited = _scope(limit=None, used=1_000.0)

    assert _event(unlimited, reserved=0.2) is None


def test_the_threshold_is_the_deployments_and_not_a_constant():
    """A deployment that moved the line is warned on its line, not on 80%."""
    scope = _scope(limit=10.0, used=4.9)

    assert _event(scope, reserved=0.2, fraction=0.5) is not None
    assert _event(scope, reserved=0.2, fraction=0.9) is None


def test_a_disabled_threshold_warns_about_nothing():
    scope = _scope(limit=10.0, used=9.9)

    assert _event(scope, reserved=0.05, fraction=0.0) is None


def test_a_window_reports_whether_it_is_approaching_its_limit():
    assert _scope(limit=10.0, used=8.5)["approaching"] is True
    assert _scope(limit=10.0, used=1.0)["approaching"] is False
    # Nothing to approach, and nothing to warn about.
    assert _scope(limit=None, used=1_000.0)["approaching"] is False


def test_a_window_that_is_already_refusing_is_not_merely_approaching():
    """`allowed` says whether work runs now; `approaching` says look soon.

    A window over its limit answers the first question, and describing it as
    approaching one as well would put a caution next to a refusal.
    """
    exhausted = _scope(limit=10.0, used=10.0)

    assert exhausted["allowed"] is False
    assert exhausted["approaching"] is False
