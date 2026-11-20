"""A schedule-started conversation names its schedule by id as well as by name.

The name can be edited and the conversation's title is derived from it, so a
list of a pod's runs that grouped them by either would split one schedule's
history in two the moment somebody renamed it.
"""

from __future__ import annotations

from uuid import uuid4

from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.services.target_start import conversation_metadata


def test_the_conversation_carries_the_schedules_id_name_and_type():
    schedule = ScheduleEntity(
        user_id=uuid4(),
        pod_id=uuid4(),
        agent_id=uuid4(),
        name="daily-invoices",
        schedule_type=ScheduleType.TIME,
    )

    metadata = conversation_metadata(schedule, {})

    # That a schedule started it is the conversation's origin, not a key.
    assert metadata == {
        "schedule_id": str(schedule.id),
        "schedule_name": "daily-invoices",
        "schedule_type": "TIME",
    }


def test_a_run_a_strangers_row_started_says_so():
    """The agent module marks such a run as reading outside text, which holds
    what it does beyond the pod's records -- the owner's own machine
    included -- for the owner's approval."""
    from app.modules.agent.contracts.automation import OUTSIDE_ROW

    schedule = ScheduleEntity(
        user_id=uuid4(),
        pod_id=uuid4(),
        agent_id=uuid4(),
        name="new-orders",
        schedule_type=ScheduleType.DATASTORE,
    )

    assert conversation_metadata(schedule, {"untrusted_row": True})[OUTSIDE_ROW]
    assert OUTSIDE_ROW not in conversation_metadata(schedule, {})
