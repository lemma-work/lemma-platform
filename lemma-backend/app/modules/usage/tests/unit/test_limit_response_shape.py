"""The limits API publishes a percentage, never an allowance in dollars.

`UsageLimitScopeResponse` used to carry `limit_usd` and `remaining_usd`. Those
state a dollar allowance, and a dollar allowance is a promise the product does
not make: what a plan includes is set per plan and may be retuned, and what a
request costs depends on the model it routes to. "You have $12.40 left" invites
a customer to plan against a number that is neither fixed nor ours to guarantee.

How much of the window is *gone* is publishable, and is the fact a caller can
act on — show a meter, warn at 80%, stop starting new work.

What the customer has actually spent (`used_usd`, `reserved_usd`) stays. It is
the boundary that is percentage-only, not the consumption.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.modules.usage.api.controllers import _limit_scope_response
from app.modules.usage.api.schemas import UsageLimitScopeResponse

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


def _scope(**overrides):
    scope = {
        "limit_usd": 100.0,
        "scope": "user_weekly",
        "used_usd": 25.0,
        "reserved_usd": 0.0,
        "remaining_usd": 75.0,
        "allowed": True,
        "reset_at": NOW + timedelta(days=7),
        "window_start": NOW,
        "counter_organization_id": None,
    }
    scope.update(overrides)
    return scope


def test_the_response_has_no_field_that_names_an_allowance():
    """The contract, asserted on the schema rather than one instance.

    Named explicitly so that re-adding either field fails here, rather than in
    whichever client eventually renders it to a customer.
    """
    fields = set(UsageLimitScopeResponse.model_fields)

    assert "limit_usd" not in fields
    assert "remaining_usd" not in fields
    assert "used_percent" in fields


def test_what_the_customer_spent_is_still_reported_in_dollars():
    """Consumption is theirs to know; only the boundary is a percentage."""
    fields = set(UsageLimitScopeResponse.model_fields)

    assert {"used_usd", "reserved_usd"} <= fields


def test_a_serialized_response_leaks_no_dollar_allowance():
    """Belt and braces: the property that matters is about the wire, not the class."""
    payload = _limit_scope_response(_scope()).model_dump()

    assert "limit_usd" not in payload
    assert "remaining_usd" not in payload
    assert payload["used_percent"] == 25.0


def test_reservations_count_against_the_window():
    """Work in flight has already been charged against the window. A meter that
    ignored it would read low exactly while a burst was landing."""
    response = _limit_scope_response(_scope(used_usd=25.0, reserved_usd=15.0))

    assert response.used_percent == 40.0


def test_an_uncapped_window_reports_no_percentage():
    """`None` is not `0.0`: "unlimited" and "nothing used yet" are different
    statements, and a meter showing 0% for an uncapped window is a lie of a
    different kind."""
    assert _limit_scope_response(_scope(limit_usd=None)).used_percent is None


def test_a_zero_cap_reads_as_fully_consumed_rather_than_dividing_by_zero():
    assert _limit_scope_response(_scope(limit_usd=0.0)).used_percent == 100.0


def test_consumption_past_the_cap_is_clamped_to_one_hundred():
    """A reservation can settle above the cap. A meter reading 130% is a bug
    report from every customer who sees it."""
    response = _limit_scope_response(_scope(limit_usd=10.0, used_usd=13.0))

    assert response.used_percent == 100.0


def test_the_other_scope_fields_still_come_through():
    response = _limit_scope_response(_scope(scope="org_monthly", allowed=False))

    assert response.scope == "org_monthly"
    assert response.allowed is False
    assert response.window_start == NOW
