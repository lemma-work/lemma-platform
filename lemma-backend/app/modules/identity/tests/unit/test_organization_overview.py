"""The organization overview's pure parts: merging what waits, filling the
days, naming what an automation's newest run opens, and the reader's zone.

What each source reads is held against Postgres in
``tests/e2e/test_organization_overview_e2e.py``; here is what is done with it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.modules.agent.contracts.conversation_summaries import (
    DayActivity,
    PendingInteraction,
    PendingInteractionKind,
    PodPendingInteraction,
)
from app.modules.channels.contracts.contacts_waiting import ContactWaiting
from app.modules.identity.domain.time_zones import known_zone, last_days
from app.modules.identity.services.organization_needs_you import (
    NeedsYouKind,
    contact_items,
    oldest_first,
    pause_items,
    wait_items,
)
from app.modules.identity.services.organization_recents import (
    AutomationKind,
    filled_days,
    newest_first,
    schedule_automation,
    workflow_automation,
)
from app.modules.schedule.contracts.fire_activity import ScheduleFireActivity
from app.modules.workflow.contracts.assigned_waits import AssignedWait
from app.modules.workflow.contracts.run_activity import WorkflowRunActivity

pytestmark = pytest.mark.unit

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
POD = uuid4()
NAMES = {POD: "Support"}


def _pause(
    conversation_id,
    *,
    ago: timedelta,
    kind=PendingInteractionKind.QUESTION,
    summary: str | None = "Which region?",
    title: str | None = "Weekly report",
) -> PodPendingInteraction:
    return PodPendingInteraction(
        pod_id=POD,
        pending=PendingInteraction(
            message_id=uuid4(),
            tool_call_id=f"call-{uuid4().hex[:6]}",
            kind=kind,
            summary=summary,
            created_at=NOW - ago,
            conversation_id=conversation_id,
            conversation_title=title,
            conversation_status=None,
            agent_id=None,
            agent_name=None,
        ),
    )


def test_a_conversation_is_one_item_aged_by_its_longest_wait():
    conversation = uuid4()
    first = _pause(conversation, ago=timedelta(hours=3))
    later = _pause(conversation, ago=timedelta(hours=1), summary="And the date?")

    [item] = pause_items([first, later], NAMES)

    assert item.key == f"conversation:{conversation}"
    assert item.since == NOW - timedelta(hours=3)
    assert item.tool_call_id == first.pending.tool_call_id
    assert item.title == "Which region?"
    assert item.detail == "Weekly report"
    assert item.pod_name == "Support"


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (PendingInteractionKind.QUESTION, NeedsYouKind.QUESTION),
        (PendingInteractionKind.APPROVAL, NeedsYouKind.APPROVAL),
        (PendingInteractionKind.PERMISSION, NeedsYouKind.PERMISSION),
    ],
)
def test_a_pause_keeps_its_kind(kind, expected):
    [item] = pause_items([_pause(uuid4(), ago=timedelta(0), kind=kind)], NAMES)
    assert item.kind is expected


def test_a_pause_that_said_nothing_is_titled_by_its_conversation():
    [item] = pause_items(
        [_pause(uuid4(), ago=timedelta(0), summary="  ", title="Invoices")], NAMES
    )
    assert (item.title, item.detail) == ("Invoices", None)


def _wait(wait_type: str, question: str | None) -> AssignedWait:
    return AssignedWait(
        wait_id=uuid4(),
        run_id=uuid4(),
        pod_id=POD,
        workflow_id=uuid4(),
        workflow_name="refund-review",
        node_id="approve",
        wait_type=wait_type,
        question=question,
        external_ref=None,
        created_at=NOW,
    )


def test_a_wait_opens_its_run_and_says_what_it_asks():
    form, decision = wait_items(
        [_wait("HUMAN", None), _wait("DECISION", "Refund it?")], NAMES
    )
    assert (form.kind, form.title, form.detail) == (
        NeedsYouKind.FORM,
        "refund-review",
        "refund-review",
    )
    assert (decision.kind, decision.title) == (NeedsYouKind.DECISION, "Refund it?")
    assert form.conversation_id is None and form.workflow_run_id is not None


def test_a_contact_is_named_and_opens_its_channel():
    channel = uuid4()
    [item] = contact_items(
        [
            ContactWaiting(
                pod_id=POD,
                channel_id=channel,
                contact_id=uuid4(),
                name="Dana",
                reason="Wants a refund",
                since=NOW,
            )
        ],
        NAMES,
    )
    assert (item.kind, item.title, item.detail) == (
        NeedsYouKind.CONTACT,
        "Dana",
        "Wants a refund",
    )
    assert item.channel_id == channel and item.key == f"contact:{channel}"


def test_the_list_is_the_oldest_first_and_stable_on_a_tie():
    older = _pause(uuid4(), ago=timedelta(days=2))
    tied = [_pause(uuid4(), ago=timedelta(hours=1)) for _ in range(2)]
    items = pause_items([*tied, older], NAMES)

    listed = oldest_first(items, limit=10)

    assert listed[0].conversation_id == older.pending.conversation_id
    assert [item.key for item in listed[1:]] == sorted(item.key for item in listed[1:])
    assert oldest_first(items, limit=1) == listed[:1]


def test_every_day_is_counted_and_a_quiet_one_is_a_zero():
    days = [date(2026, 3, 8), date(2026, 3, 9), date(2026, 3, 10)]
    filled = filled_days(
        days,
        [DayActivity(pod_id=POD, day=date(2026, 3, 9), by_agent=4, by_people=1)],
    )
    assert [(each.day, each.by_agent, each.by_people) for each in filled[POD]] == [
        (date(2026, 3, 8), 0, 0),
        (date(2026, 3, 9), 4, 1),
        (date(2026, 3, 10), 0, 0),
    ]


def _fire(target_kind: str, target_run_id: str | None) -> ScheduleFireActivity:
    return ScheduleFireActivity(
        pod_id=POD,
        schedule_id=uuid4(),
        schedule_name="morning-digest",
        fire_count=3,
        latest_fire_id=uuid4(),
        latest_status="COMPLETED",
        latest_fire_at=NOW,
        latest_target_kind=target_kind,
        latest_target_run_id=target_run_id,
    )


def test_a_schedule_opens_what_its_newest_fire_started():
    conversation, run = uuid4(), uuid4()
    agent = schedule_automation(_fire("AGENT", str(conversation)))
    workflow = schedule_automation(_fire("WORKFLOW", str(run)))
    not_yet = schedule_automation(_fire("AGENT", None))
    garbled = schedule_automation(_fire("AGENT", "not-a-uuid"))

    assert (agent.conversation_id, agent.workflow_run_id) == (conversation, None)
    assert (workflow.conversation_id, workflow.workflow_run_id) == (None, run)
    assert not_yet.conversation_id is None and garbled.conversation_id is None
    assert agent.kind is AutomationKind.SCHEDULE and agent.run_count == 3


def test_automations_are_listed_by_their_newest_run():
    run = WorkflowRunActivity(
        pod_id=POD,
        workflow_id=uuid4(),
        workflow_name="triage",
        run_count=7,
        latest_run_id=uuid4(),
        latest_run_status="RUNNING",
        latest_run_at=NOW - timedelta(minutes=5),
    )
    workflow = workflow_automation(run)
    schedule = schedule_automation(_fire("AGENT", None))

    assert newest_first([workflow, schedule]) == [schedule, workflow]
    assert workflow.workflow_run_id == run.latest_run_id
    assert workflow.kind is AutomationKind.WORKFLOW


def test_a_zone_is_an_iana_name_this_host_knows():
    assert known_zone("Europe/Berlin") == ZoneInfo("Europe/Berlin")
    assert known_zone("") == ZoneInfo("UTC")
    assert known_zone(None) == ZoneInfo("UTC")
    for bad in ("europe/berlin", "Mars/Olympus", "../etc/passwd"):
        with pytest.raises(ValueError):
            known_zone(bad)


def test_the_days_are_the_readers_calendar_days():
    # Late evening in New York is already tomorrow in UTC.
    evening = datetime(2026, 3, 10, 2, 30, tzinfo=timezone.utc)
    days, since = last_days(now=evening, zone=ZoneInfo("America/New_York"), days=3)
    assert days == [date(2026, 3, 7), date(2026, 3, 8), date(2026, 3, 9)]
    assert since == datetime(2026, 3, 7, tzinfo=ZoneInfo("America/New_York"))


def test_a_clock_change_moves_no_days_boundary():
    # New York's clocks go forward on the second Sunday of March: that day is
    # 23 hours long, and the window still starts at its midnight.
    after = datetime(2026, 3, 9, 15, 0, tzinfo=timezone.utc)
    days, since = last_days(now=after, zone=ZoneInfo("America/New_York"), days=2)
    assert days == [date(2026, 3, 8), date(2026, 3, 9)]
    assert since.astimezone(timezone.utc) == datetime(
        2026, 3, 8, 5, 0, tzinfo=timezone.utc
    )
