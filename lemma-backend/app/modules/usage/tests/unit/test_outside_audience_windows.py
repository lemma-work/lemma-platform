"""What a run answering somebody outside the organization is held to.

Such a run is the organization's spend: it must never use up the personal
allowance of the member who looks after the conversation, and it counts towards
the contacts cap an organization admin sets -- which nothing else counts
towards.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.modules.usage.contracts import outside_audience_source, run_source_type
from app.modules.usage.domain.accounting import (
    CONTACT_RUN,
    OUTSIDE_AUDIENCE_SOURCES,
    OUTSIDER_RUN,
    MeteringIdentity,
)
from app.modules.usage.domain.budget_windows import budget_windows
from app.modules.usage.domain.ports import UsageLimitValues
from app.modules.usage.services.metering_scope import MeteringScope
from app.modules.usage.services.usage_context import UsageExecutionContext

pytestmark = pytest.mark.unit

ORG = uuid4()
NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
LIMITS = UsageLimitValues(
    org_monthly_limit_usd=100.0,
    user_weekly_limit_usd=5.0,
    user_monthly_limit_usd=10.0,
    contacts_monthly_limit_usd=20.0,
)


def _identity(source_type: str, *, scope: str = "SYSTEM") -> MeteringIdentity:
    return MeteringIdentity(
        execution_id=uuid4(),
        organization_id=ORG,
        user_id=uuid4(),
        source_type=source_type,
        profile_id="p",
        profile_scope=scope,
        model_name="m",
        provider_model_name="m",
    )


def _kinds(source_type: str) -> set[str]:
    return {
        window.kind
        for window in budget_windows(_identity(source_type), LIMITS, NOW)
        if window.limit is not None
    }


def test_a_members_run_is_held_to_the_member_and_the_organization():
    assert _kinds("agent_run") == {"org_month", "user_week", "user_month"}


@pytest.mark.parametrize("source", [CONTACT_RUN, OUTSIDER_RUN])
def test_a_run_for_somebody_outside_never_spends_the_members_allowance(source):
    assert _kinds(source) == {"org_month", "contacts_month"}


def test_the_contacts_window_counts_only_runs_for_people_outside():
    [window] = [
        window
        for window in budget_windows(_identity(CONTACT_RUN), LIMITS, NOW)
        if window.kind == "contacts_month"
    ]

    assert window.organization_id == ORG
    assert window.user_id is None
    assert window.source_types == OUTSIDE_AUDIENCE_SOURCES
    assert window.start == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert window.end == datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_without_a_cap_the_contacts_window_limits_nothing():
    uncapped = UsageLimitValues(org_monthly_limit_usd=100.0)

    windows = budget_windows(_identity(CONTACT_RUN), uncapped, NOW)

    assert [w.limit for w in windows if w.kind == "contacts_month"] == [None]


def test_an_organizations_own_key_is_not_metered_at_all():
    assert (
        budget_windows(_identity(CONTACT_RUN, scope="ORGANIZATION"), LIMITS, NOW) == []
    )


def test_a_run_is_recorded_under_whom_it_answers():
    assert run_source_type(answers_outsider=False, answers_contact=False) == "agent_run"
    assert run_source_type(answers_outsider=True, answers_contact=False) == OUTSIDER_RUN
    assert run_source_type(answers_outsider=True, answers_contact=True) == CONTACT_RUN
    assert (
        outside_audience_source(answers_outsider=False, answers_contact=False) is None
    )


def test_the_members_windows_leave_out_what_answering_outsiders_cost():
    """The ledger sums a window by user, so an outside run recorded under the
    member who looks after it would otherwise still fill their allowance."""
    windows = budget_windows(_identity("agent_run"), LIMITS, NOW)

    for window in windows:
        expected = OUTSIDE_AUDIENCE_SOURCES if window.user_id is not None else ()
        assert window.excluded_source_types == expected, window.kind


# --------------------------------------------- work done for an outside run


def _context(
    source_type: str = "agent_run", *, outside_audience: str | None = None
) -> UsageExecutionContext:
    return UsageExecutionContext(
        user_id=uuid4(),
        organization_id=ORG,
        pod_id=uuid4(),
        source_type=source_type,
        outside_audience=outside_audience,
    )


def _scope(context, parent=None) -> MeteringScope:
    return MeteringScope(context, factory=None, settings=None, parent=parent)


def test_an_outside_runs_context_knows_its_audience_apart_from_its_source():
    context = _context(CONTACT_RUN)

    assert context.outside_audience == CONTACT_RUN
    # What a vision delegate does: copy the run's context under its own source.
    vision = replace(context, source_type="vision")
    assert vision.source_type == "vision"
    assert vision.outside_audience == CONTACT_RUN
    assert _context().outside_audience is None


def test_nothing_but_an_outside_source_can_be_an_outside_audience():
    with pytest.raises(ValueError):
        _context(outside_audience="vision")


@pytest.mark.parametrize("source", ["vision", "history_compaction", None])
def test_spend_for_an_outside_run_is_recorded_as_that_run_whatever_it_bought(
    source,
):
    """Vision, compaction and titles name a source of their own. Under a
    contact's run that source used to replace ``contact_run``, so the spend
    left the contacts cap and landed on the member's allowance."""
    scope = _scope(_context(CONTACT_RUN))

    assert scope.recorded_source(source) == CONTACT_RUN
    assert _kinds(scope.recorded_source(source)) == {"org_month", "contacts_month"}


def test_a_members_run_still_records_what_each_request_was_for():
    scope = _scope(_context())

    assert scope.recorded_source("vision") == "vision"
    assert scope.recorded_source(None) == "agent_run"


def test_an_execution_opened_inside_an_outside_run_inherits_its_audience():
    """A nested execution with a context of its own -- one that never heard of
    the audience -- is still the outside run's spend."""
    outer = _scope(_context(OUTSIDER_RUN))
    inner = _scope(_context("conversation_title"), parent=outer)

    assert inner.recorded_source(None) == OUTSIDER_RUN
    assert inner.recorded_source("vision") == OUTSIDER_RUN
