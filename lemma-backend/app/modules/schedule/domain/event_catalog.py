"""Every hook point standing work can attach to, in one list.

Shaped like an MCP Events descriptor -- a name, what it means, the arguments
that narrow it, what it carries -- because the same list is what a pod offers
to outside subscribers and what a connected server's events are recorded as.
The names are the hook API: `record.created` is what a code hook will one day
attach to, so it is named for what happened, not for the table it came from.

Each is served by a schedule type of its own: `time` is TIME, `record.*` is
DATASTORE, and a connected MCP server's events (listed beside these by the
controller, per caller) are WEBHOOK.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import JsonValue

from app.modules.schedule.domain.schedule import ScheduleType


@dataclass(frozen=True, slots=True)
class EventDescriptor:
    name: str
    title: str
    description: str
    schedule_type: ScheduleType
    input_schema: dict[str, JsonValue] = field(default_factory=dict)
    payload_schema: dict[str, JsonValue] = field(default_factory=dict)


BUILT_IN_EVENTS: tuple[EventDescriptor, ...] = (
    EventDescriptor(
        name="time",
        title="On a schedule",
        description="A time, or a repeating one, in a time zone.",
        schedule_type=ScheduleType.TIME,
        input_schema={
            "type": "object",
            "properties": {
                "cron": {"type": "string"},
                "scheduled_at": {"type": "string"},
                "timezone": {"type": "string"},
            },
        },
    ),
    *(
        EventDescriptor(
            name=f"record.{verb}",
            title=f"A row is {verb} in a table",
            description=f"A row of the named table is {verb}.",
            schedule_type=ScheduleType.DATASTORE,
            input_schema={
                "type": "object",
                "required": ["table"],
                "properties": {"table": {"type": "string"}},
            },
        )
        for verb in ("created", "updated", "deleted")
    ),
)
