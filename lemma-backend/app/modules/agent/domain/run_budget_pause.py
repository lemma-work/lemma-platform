"""A run that has spent its budget, expressed as an ordinary Lemma approval.

Rendering it as a ``request_approval`` tool call is what lets one approval path
serve every entry point: the web client reads the persisted call, Slack, Teams
and Telegram render native buttons from it, and all of them resolve through the
same endpoint. This is the pattern ``agent_host_permissions`` already
established, for the same reason — the alternative is a new pause with a new
delivery path to every surface.

Why an approval rather than a new ``AgentWaitType``: ``domain/wait`` says it
outright — human pauses resolve through the approval-decision row, which records
*who* decided. A wait row is armed with a ``scheduled_at`` and swept, and a
budget pause has no time at which it should resolve itself. ``PS-AGENT-020``
already promises an approval pause is held indefinitely rather than timing out.

Two outcomes, from three decisions:

* approve once     — one more budget, then ask again
* approve always   — the same; see below
* deny             — stop here and report what you have

``APPROVE_FOR_SESSION`` deliberately does *not* stop the asking. Everywhere else
it means "this exact call, again, without me" and is recorded per permission
against the tool being approved; there is no tool here, and the thing it would
switch off is the only guard against a run that has stopped converging. Held to
its usual meaning it would retire that guard for the rest of the conversation on
one click, which is a much larger decision than the button appears to offer.

Renewing instead is the safe reading of "yes, keep going", and it costs little
now that a budget is a backstop rather than a schedule: the next ask is another
full allowance away. If "stop asking" is ever wanted it needs to be its own
choice, worded as what it does.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.modules.agent.domain.run_budget import BudgetExhausted
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    JsonObject,
    MessageDraft,
)

#: Key under a ``request_approval`` call's args identifying it as a budget
#: pause. Its presence is the single discriminator that routes the decision to
#: "carry on" rather than to a tool the agent asked to run.
RUN_BUDGET_KEY = "run_budget_pause"

_TITLE = "Keep going?"

#: What a person is offered. Deliberately not the tool's own wording ("approve
#: this action"): nothing is being approved, a spend is being extended.
_QUESTION = (
    "{reason}\n\nContinue, and it gets the same allowance again; stop, and it "
    "reports what it has so far."
)


def budget_pause_tool_call_id() -> str:
    """A fresh id, namespaced so it cannot collide with a model's own call.

    A budget trip happens *between* model calls, so unlike every other pause
    there is no tool call to attach to — the whole resume path keys on
    resolving a pending call, so one has to be minted. The precedent is
    ``mcp_pausing_calls``, which mints ids for the same reason.
    """
    return f"run-budget:{uuid4().hex}"


def budget_pause_tool_args(exhausted: BudgetExhausted) -> JsonObject:
    """``request_approval`` arguments, in the shape every renderer expects.

    ``title`` / ``reason`` / ``tool_name`` match what the real tool writes, so
    web cards and the surface approval plan need no special case. There is no
    ``args`` key: nothing here is executed, which is also what stops the
    approval executor trying to run a tool named on the card.
    """
    return {
        "title": _TITLE,
        "reason": _QUESTION.format(reason=exhausted.reason),
        "tool_name": "continue_running",
        RUN_BUDGET_KEY: {
            "dimension": exhausted.dimension.value,
            "spent": exhausted.spent,
        },
    }


def is_budget_pause(tool_args: object) -> bool:
    """Whether a persisted ``request_approval`` call is one of these."""
    return isinstance(tool_args, dict) and RUN_BUDGET_KEY in tool_args


def budget_pause_events(
    *,
    agent_run_id: UUID,
    tool_call_id: str,
    exhausted: BudgetExhausted,
    sequence: int,
) -> list[AgentEvent]:
    """The events a budget trip becomes: the card, then the run ending.

    No STATUS event, unlike the Agent Host permission it is modelled on: that
    one continues a live run and needs a surface to render buttons mid-turn,
    while this one ends the run, and the WAITING the harness emits afterwards is
    what tells everything downstream the turn is over.
    """
    return [
        AgentEvent(
            type=AgentEventType.MESSAGE,
            data=MessageDraft.of_tool_call(
                tool_name="request_approval",
                tool_call_id=tool_call_id,
                tool_args=budget_pause_tool_args(exhausted),
            ),
            agent_run_id=agent_run_id,
            sequence=sequence,
        )
    ]
