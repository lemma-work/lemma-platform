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

    assert metadata == {
        "started_by": "SCHEDULE",
        "schedule_id": str(schedule.id),
        "schedule_name": "daily-invoices",
        "schedule_type": "TIME",
    }
