"""What a schedule may wake, and what it must say when it does.

A schedule's target used to be two nullable foreign keys, and "exactly one of
them" was the whole rule. The pod's default assistant is a third arm that no
foreign key can express — it has no ``agents`` row, because a conversation with
a null ``agent_id`` *is* the assistant — so the rules gained enough shape to be
worth stating in one place, next to ``schedule_update_policy`` and
``time_schedule_policy`` rather than inside the service that calls all three.

Two rules live here:

*Exactly one target.* Three columns now, so "set the new one" is not enough —
the other two have to be cleared in the same breath, or a schedule ends up
pointed at two things and fires twice. The table's check constraint refuses
that state; these helpers are what stop it being reached.

*The assistant must be told what to do.* A named agent is its own standing
instruction, so a schedule may add to it or say nothing at all. The default
assistant's instruction is the empty string — it is whoever the conversation
needs it to be — so a schedule that wakes it and says nothing wakes it to a
trigger payload with no job. That is refused at save time rather than
discovered at 9am, which is the same call ``DatastoreScheduleConfig`` makes
about conditions no operation could satisfy.
"""

from __future__ import annotations

from app.modules.schedule.domain.errors import ScheduleValidationError
from app.modules.schedule.domain.interfaces import ScheduleTarget
from app.modules.schedule.domain.schedule import (
    INSTRUCTION_REQUIRED,
    ScheduleCreateEntity,
    ScheduleEntity,
    ScheduleUpdateEntity,
    is_pod_default_agent_target,
)


def pod_default_target_fields() -> dict[str, object]:
    """The three target columns, for the default assistant."""
    return {"agent_id": None, "workflow_id": None, "targets_pod_default": True}


def named_agent_target_fields(agent_id) -> dict[str, object]:
    """The three target columns, for an agent with a row."""
    return {"agent_id": agent_id, "workflow_id": None, "targets_pod_default": False}


def workflow_target_fields(workflow_id) -> dict[str, object]:
    """The three target columns, for a workflow."""
    return {
        "agent_id": None,
        "workflow_id": workflow_id,
        "targets_pod_default": False,
    }


def validate_single_target(schedule_create: ScheduleCreateEntity) -> bool:
    """Whether this schedule has a target at all; raise if it has two.

    Returning the "has any" answer rather than nothing lets the caller skip the
    per-target lookups for a schedule that wakes nothing, which internal
    (wait/timeout) schedules legitimately are.
    """
    targets = (
        schedule_create.agent_id is not None,
        schedule_create.workflow_id is not None,
        schedule_create.targets_pod_default,
    )
    if sum(targets) > 1:
        raise ScheduleValidationError(
            "Schedule can target either an agent or workflow, not both"
        )
    return any(targets)


def validate_pod_default_instruction(schedule_create: ScheduleCreateEntity) -> None:
    """The authoritative copy of the rule the create request also checks.

    ``CreateScheduleRequest`` gives an HTTP caller a field-scoped 422, but
    bundle import builds the entity directly and never sees it — and a
    hand-written bundle is exactly where a schedule with nothing to do would
    come from.
    """
    if schedule_create.targets_pod_default and not _said(schedule_create.instruction):
        raise ScheduleValidationError(INSTRUCTION_REQUIRED)


def validate_instruction_survives_update(
    existing: ScheduleEntity,
    schedule_update: ScheduleUpdateEntity,
) -> None:
    """An edit that leaves the target alone can still empty the instruction.

    The request schema cannot see what the schedule already points at; this
    can. Retargeting *away* from the assistant is unconstrained — a named agent
    or a workflow does not need the sentence, so dropping it loses nothing.
    """
    if schedule_update.instruction is None:
        return
    if _retargeting_away(schedule_update):
        return
    stays_pod_default = existing.targets_pod_default or is_pod_default_agent_target(
        schedule_update.agent_name
    )
    if stays_pod_default and not _said(schedule_update.instruction):
        raise ScheduleValidationError(INSTRUCTION_REQUIRED)


def derive_webhook_target_from_workflow_start(
    workflow: ScheduleTarget,
    *,
    config: dict,
    requested_connector_trigger_id: str | None,
) -> dict:
    """How a WEBHOOK schedule is wired to a workflow it wakes.

    A workflow listens to the app event its own start already names, so the
    schedule takes the connector trigger from there rather than being told one
    — being told one is refused outright, because two answers to "which event"
    is how a schedule ends up listening to something its target cannot handle.
    """
    if workflow.event_trigger_id is None:
        raise ScheduleValidationError(
            "Webhook workflow schedules require an EVENT workflow start"
        )
    if requested_connector_trigger_id:
        raise ScheduleValidationError(
            "connector_trigger_id is only valid for agent webhook schedules; "
            "workflow webhook schedules derive it from the workflow event start"
        )

    config = dict(config or {})
    trigger_config = dict(workflow.event_trigger_config or {})
    conflicting_keys = sorted(
        key
        for key, value in trigger_config.items()
        if key in config and config[key] != value
    )
    if conflicting_keys:
        raise ScheduleValidationError(
            "Schedule config conflicts with workflow event start trigger_config "
            f"for: {', '.join(conflicting_keys)}"
        )
    config.update(trigger_config)

    return {
        "connector_trigger_id": workflow.event_trigger_id,
        "config": config,
    }


def _retargeting_away(schedule_update: ScheduleUpdateEntity) -> bool:
    return bool(schedule_update.workflow_name) or (
        bool(schedule_update.agent_name)
        and not is_pod_default_agent_target(schedule_update.agent_name)
    )


def _said(instruction: str | None) -> bool:
    """Whitespace is not an instruction."""
    return bool((instruction or "").strip())
