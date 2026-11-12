"""Synthetic tool returns emitted when a harness terminates mid-call."""

from dataclasses import dataclass, field
from uuid import UUID

from app.modules.agent.domain.message_state import MessageState
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    MessageDraft,
    MessageKind,
)
from app.modules.agent.domain.pauses import CALL_NAMES
from app.modules.agent.domain.state_kinds import PauseKind


def missing_tool_return_events(
    *,
    outstanding_tool_calls: dict[str, str],
    terminal_event: AgentEvent,
) -> list[AgentEvent]:
    events = [
        AgentEvent(
            type=AgentEventType.MESSAGE,
            data=MessageDraft.of_tool_return(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_result={
                    "success": False,
                    "error": _missing_tool_return_error(terminal_event.type),
                    **(
                        {"interaction_fallback": True}
                        if tool_name
                        in {
                            CALL_NAMES[PauseKind.QUESTION],
                            CALL_NAMES[PauseKind.APPROVAL],
                        }
                        else {}
                    ),
                },
                metadata={"terminal_event": terminal_event.type.value},
                state=MessageState(synthetic_tool_return=True),
            ),
            agent_run_id=terminal_event.agent_run_id,
        )
        for tool_call_id, tool_name in outstanding_tool_calls.items()
    ]
    outstanding_tool_calls.clear()
    return events


def _missing_tool_return_error(event_type: AgentEventType) -> str:
    if event_type == AgentEventType.STOPPED:
        return "The agent run stopped before the tool returned a result."
    if event_type == AgentEventType.ERROR:
        return "The agent run failed before the tool returned a result."
    if event_type == AgentEventType.REJECTED:
        return "The agent run was rejected before the tool returned a result."
    if event_type == AgentEventType.WAITING:
        return "The agent run paused for user input before the tool returned a result."
    return "The agent run completed without a tool return."


@dataclass(slots=True)
class OutstandingToolCalls:
    """Tool calls this run announced and has not answered yet.

    A run that dies between a tool executing and its result being recorded
    leaves a call with no return. The next run rebuilds history, finds the
    orphan, and `pydantic_ai_history._build_tool_batch` synthesizes "This tool
    call was interrupted before a result was recorded... Run it again if you
    still need the result." So a send that *succeeded* is reported to the model
    as never having happened, and the model is told to do it again: a duplicate
    email, a duplicate record write, a destructive command run twice.

    The remote harness has closed its outstanding calls since it was written.
    The in-process one declared this bookkeeping and never used it.
    """

    _calls: dict[str, str] = field(default_factory=dict)

    def observe(self, event: AgentEvent) -> None:
        """Track a tool call, or forget one that has just been answered."""
        draft = event.data
        if event.type is not AgentEventType.MESSAGE:
            return
        if not isinstance(draft, MessageDraft):
            return
        tool_call_id = draft.tool_call_id
        if not tool_call_id:
            return
        if draft.kind is MessageKind.TOOL_CALL:
            self._calls[tool_call_id] = draft.tool_name or "unknown"
        elif draft.kind is MessageKind.TOOL_RETURN:
            self._calls.pop(tool_call_id, None)

    def closing_events(
        self,
        terminal_event: AgentEvent,
        *,
        skip_tool_call_ids: frozenset[str] = frozenset(),
    ) -> list[AgentEvent]:
        """Returns that close whatever is still open, given how the run ended.

        `skip_tool_call_ids` are the pausing calls on the WAITING path: each
        gets its real return appended when it is resolved, so closing it here
        would duplicate it.
        """
        outstanding = {
            call_id: name
            for call_id, name in self._calls.items()
            if call_id not in skip_tool_call_ids
        }
        self._calls.clear()
        return missing_tool_return_events(
            outstanding_tool_calls=outstanding, terminal_event=terminal_event
        )


def waiting_event(
    calls: list[tuple[str, str]], *, conversation_id: str, agent_run_id: UUID
) -> AgentEvent:
    """The run ends waiting on ``calls``: each pause's ``(tool_call_id, kind)``.

    ``tool_call_id`` and ``kind`` name the first, as a WAITING event always has;
    ``tool_call_ids`` names every one the step opened.
    """
    first_id, first_kind = calls[0]
    return AgentEvent(
        type=AgentEventType.WAITING,
        data={
            "tool_call_id": first_id,
            "kind": first_kind,
            "tool_call_ids": [call_id for call_id, _ in calls],
            "conversation_id": conversation_id,
        },
        agent_run_id=agent_run_id,
    )


def paused_call_ids(terminal: AgentEvent) -> frozenset[str]:
    """The calls a WAITING terminal leaves open for their real returns."""
    if terminal.type is not AgentEventType.WAITING or not isinstance(
        terminal.data, dict
    ):
        return frozenset()
    ids = terminal.data.get("tool_call_ids")
    if isinstance(ids, list):
        return frozenset(str(call_id) for call_id in ids)
    first = terminal.data.get("tool_call_id")
    return frozenset({str(first)}) if first else frozenset()
