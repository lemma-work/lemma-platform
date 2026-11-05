"""What an automated run is told, and what a workflow reads back from it.

A schedule's run opens on a sentence naming what fired and when, the task,
then the event body; a workflow step's on its input as JSON, which its nodes
bind to. ``step_output`` is the conversation's output as a workflow step's
node output. Pure: ``application/automation`` writes these.
"""

from __future__ import annotations

import json

from app.modules.agent.domain.run_state import ScheduleFiring

#: Keys of a firing's ``input_data["metadata"]`` that describe the *firing*,
#: not the event, written by `ScheduleStartService._build_trigger`. Rendered as
#: a sentence at the top of the wake message, so they are dropped from the
#: event body below it rather than printed twice -- and recorded on the run as
#: its ``schedule_firing``.
_FIRING_FACTS = frozenset(
    {
        "schedule_id",
        "schedule_name",
        "trigger_type",
        "fired_at",
        "scheduled_for",
        "timezone",
    }
)


def schedule_firing(
    input_data: dict[str, object],
    conversation_metadata: dict[str, object],
    schedule_type: str | None,
) -> ScheduleFiring | None:
    """The facts about the firing that started this run, as the run keeps them."""
    raw = input_data.get("metadata")
    facts = {
        key: value
        for key, value in (raw if isinstance(raw, dict) else {}).items()
        if key in _FIRING_FACTS
    }
    facts.setdefault("schedule_id", conversation_metadata.get("schedule_id"))
    facts.setdefault("schedule_name", conversation_metadata.get("schedule_name"))
    facts.setdefault("trigger_type", schedule_type)
    return ScheduleFiring.from_json(facts)


def step_output(output: object) -> dict[str, object]:
    if isinstance(output, dict):
        return output
    if output is None or output == "":
        return {}
    return {"answer": output}


def conversation_title(
    agent_name: str, input_data: dict[str, object], started_by_schedule: bool
) -> str:
    if started_by_schedule:
        metadata = input_data.get("metadata")
        name = metadata.get("schedule_name") if isinstance(metadata, dict) else None
        if isinstance(name, str) and name.strip():
            return f"Schedule: {name.strip()}"
        return f"Scheduled run: {agent_name}"
    return f"Workflow run: {agent_name}"


def workflow_input_prompt(input_data: dict[str, object]) -> str:
    payload = json.dumps(input_data, ensure_ascii=True, indent=2, default=str)
    return f"Workflow input JSON:\n{payload}"


def _firing_sentences(metadata: dict[str, object]) -> list[str]:
    """What fired, and when, as prose rather than as JSON keys."""

    def _text(key: str) -> str | None:
        value = metadata.get(key)
        return value if isinstance(value, str) and value.strip() else None

    name = _text("schedule_name")
    lines = [
        f'The schedule "{name}" started this run.'
        if name
        else "A schedule started this run."
    ]

    fired_at = _text("fired_at")
    scheduled_for = _text("scheduled_for")
    timezone_name = _text("timezone")
    when = []
    if fired_at:
        when.append(f"Fired at {fired_at}.")
    # Only worth saying when it differs: for a fire that ran on time the two
    # are the same instant, and printing both invites the agent to wonder
    # which one matters.
    if scheduled_for and scheduled_for != fired_at:
        when.append(f"This is the occurrence due at {scheduled_for}.")
    if timezone_name:
        when.append(f"The schedule is set in {timezone_name}; times above are UTC.")
    if when:
        lines.append(" ".join(when))
    return lines


def _firing_event_body(
    input_data: dict[str, object], metadata: dict[str, object]
) -> dict[str, object]:
    """The event itself, with the empty parts and the firing facts removed.

    A TIME firing has nothing left here, which is the point: it gets a
    sentence and a task rather than three empty objects. A webhook or
    datastore firing still carries its delivery or its row in full.
    """
    body = {
        key: value for key, value in input_data.items() if key != "metadata" and value
    }
    event_metadata = {
        key: value
        for key, value in metadata.items()
        if key not in _FIRING_FACTS and value
    }
    if event_metadata:
        body["metadata"] = event_metadata
    return body


def schedule_wake_prompt(
    input_data: dict[str, object], instructions: str | None
) -> str:
    """What a schedule-started agent reads as its first turn.

    The workflow rendering was wrong here in a way nothing caught, because
    no test pinned this text. A cron-fired agent's opening message was:

        Workflow input JSON:
        {"payload": {}, "metadata": {}, "llm_output": {}}

    -- three empty objects, under a heading naming a workflow that does not
    exist, for a run started by a clock. Everything telling the agent why it
    had woken was in the system prompt, where it reads as standing
    configuration rather than as the thing that just happened.

    So: say what fired and when, then the task, then the event body.

    `instructions` is repeated here deliberately. The system prompt's
    `# Conversation Instructions` is what survives into later turns of a
    multi-turn run; this is what the first turn is answering.
    """
    raw = input_data.get("metadata")
    metadata = raw if isinstance(raw, dict) else {}
    lines = _firing_sentences(metadata)

    if instructions and instructions.strip():
        lines.append(f"What to do:\n{instructions.strip()}")

    body = _firing_event_body(input_data, metadata)
    if body:
        rendered = json.dumps(body, ensure_ascii=True, indent=2, default=str)
        lines.append(f"Event data:\n{rendered}")
    elif metadata.get("trigger_type") == "TIME":
        lines.append("There is no event data: this firing is the time arriving.")

    return "\n\n".join(lines)
