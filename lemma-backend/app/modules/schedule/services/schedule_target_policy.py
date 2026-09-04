"""What a schedule may wake, and what it must say when it does.

Two rules live here:

*Exactly one target.* Two nullable foreign keys, and "set the new one" is not
enough — the other has to be cleared in the same breath, or a schedule ends up
pointed at two things and fires twice. The table's check constraint refuses
that state; these helpers are what stop it being reached.

*A target with nothing to say for itself must be told what to do.* An agent's
standing instruction says what it is *for*, and a schedule's says what *this
firing* is for; an agent that has the first may be woken without the second.
The pod's own assistant has no standing instruction — it is whoever the
conversation needs it to be — so waking it without one wakes it to a trigger
payload and no job.

That rule is stated against the agent's instruction rather than against which
agent it is, which is both more general and, today, exactly equivalent: a blank
instruction is refused everywhere an agent can be created, so the assistant is
the only agent that can have one. Being keyed on data rather than on identity
is why this module no longer needs to know what a pod default agent is.

The check moved from the request schema to here when the assistant gained a
row. A Pydantic validator cannot look one up, so it could only ever have
recognised the *name*; this sees the resolved target and asks it directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.core.authorization.context import ResourceRef, ResourceType
from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_NAME,
    POD_DEFAULT_AGENT_SELECTOR_ALIASES,
    is_pod_default_agent,
)
from app.modules.schedule.domain.errors import ScheduleValidationError
from app.modules.schedule.domain.interfaces import ScheduleTarget
from app.modules.schedule.domain.schedule import (
    INSTRUCTION_REQUIRED,
    ScheduleCreateEntity,
    ScheduleEntity,
    ScheduleUpdateEntity,
)


def normalize_agent_selector(agent_name: str) -> str:
    """The wire selector for the pod's assistant, resolved to its row's name.

    `POD_DEFAULT` is what the API, the CLI and every exported bundle say, and it
    is not a name -- it predates the assistant having one. Normalising here is
    what lets a single `get_by_pod_and_name` serve the assistant and every other
    agent, instead of the caller branching on which it is.
    """
    if agent_name in POD_DEFAULT_AGENT_SELECTOR_ALIASES:
        return DEFAULT_POD_AGENT_NAME
    return agent_name


def agent_execute_ref(agent_id: UUID, *, pod_id: UUID) -> ResourceRef:
    """What `agent.execute` is asked about for this target."""
    if is_pod_default_agent(agent_id, pod_id=pod_id):
        return ResourceRef.pod(pod_id)
    return ResourceRef(
        resource_type=ResourceType.AGENT,
        resource_id=agent_id,
        pod_id=pod_id,
    )


def named_agent_target_fields(agent_id) -> dict[str, object]:
    """Both target columns, for an agent."""
    return {"agent_id": agent_id, "workflow_id": None}


def workflow_target_fields(workflow_id) -> dict[str, object]:
    """Both target columns, for a workflow."""
    return {"agent_id": None, "workflow_id": workflow_id}


def validate_single_target(schedule_create: ScheduleCreateEntity) -> bool:
    """Whether this schedule has a target at all; raise if it has two.

    Returning the "has any" answer rather than nothing lets the caller skip the
    per-target lookups for a schedule that wakes nothing, which internal
    (wait/timeout) schedules legitimately are.
    """
    targets = (
        schedule_create.agent_id is not None,
        schedule_create.workflow_id is not None,
    )
    if sum(targets) > 1:
        raise ScheduleValidationError(
            "Schedule can target either an agent or workflow, not both"
        )
    return any(targets)


def validate_global_workflow_is_unclaimed(
    conflicting: Sequence[ScheduleEntity],
) -> None:
    """A GLOBAL workflow gets one schedule per user per pod, and no more.

    Create and update both have to enforce it; stating it once is what keeps
    them from naming different schedules as the conflict.
    """
    if not conflicting:
        return
    raise ScheduleValidationError(
        "Global workflow already has a schedule for this user in this "
        "pod (GLOBAL workflows are system-wide singletons). "
        f"Conflicting schedule: '{conflicting[0].name}' "
        f"({conflicting[0].id}). Update or delete it instead of "
        "creating another."
    )


def validate_target_instruction(agent, instruction: str | None) -> None:
    """A target that carries no purpose of its own must be given one here.

    ``agent`` is the resolved row, not a name -- which is the whole reason this
    lives in the service layer rather than in the request schema.
    """
    if agent is not None and not _said(agent.instruction) and not _said(instruction):
        raise ScheduleValidationError(INSTRUCTION_REQUIRED)


async def target_agent_after_update(resolver, existing, update_data: dict):
    """The agent this schedule will point at once the update lands.

    None when it will point at a workflow or at nothing -- neither has a
    standing instruction to be asked about.
    """
    agent_id = update_data.get("agent_id", existing.agent_id)
    if agent_id is None:
        return None
    return await resolver.get_agent(agent_id)


def validate_instruction_survives_update(
    agent,
    schedule_update: ScheduleUpdateEntity,
) -> None:
    """An edit that leaves the target alone can still empty the instruction.

    ``agent`` is whatever the schedule will point at once this update lands.
    Retargeting onto something with a standing instruction of its own is
    unconstrained -- dropping the sentence there loses nothing.
    """
    if schedule_update.instruction is None:
        return
    if _retargeting_away(schedule_update):
        return
    validate_target_instruction(agent, schedule_update.instruction)


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
    """Whether this update points the schedule at a workflow instead.

    A workflow is its own set of steps and has nowhere to put a sentence, so it
    never needs one.
    """
    return bool(schedule_update.workflow_name)


def _said(instruction: str | None) -> bool:
    """Whitespace is not an instruction."""
    return bool((instruction or "").strip())
