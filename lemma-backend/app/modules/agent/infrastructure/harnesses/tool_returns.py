"""Synthetic tool returns emitted when a harness terminates mid-call."""

from dataclasses import dataclass, field

from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    MessageDraft,
    MessageKind,
)

TERMINAL_TOOL_EVENT_TYPES = frozenset(
    {
        AgentEventType.ERROR,
        AgentEventType.STOPPED,
        AgentEventType.REJECTED,
    }
)


@dataclass(slots=True)
class OutstandingToolCalls:
    calls: dict[str, str] = field(default_factory=dict)

    def observe(self, event: AgentEvent) -> None:
        if event.type is not AgentEventType.MESSAGE:
            return
        message = event.data
        tool_call_id = getattr(message, "tool_call_id", None)
        if not isinstance(tool_call_id, str) or not tool_call_id:
            return
        if getattr(message, "kind", None) is MessageKind.TOOL_CALL:
            tool_name = getattr(message, "tool_name", None)
            self.calls[tool_call_id] = (
                tool_name
                if isinstance(tool_name, str) and tool_name
                else "unknown_tool"
            )
        elif getattr(message, "kind", None) is MessageKind.TOOL_RETURN:
            self.calls.pop(tool_call_id, None)

    def discard(self, tool_call_id: str) -> None:
        self.calls.pop(tool_call_id, None)

    def close(self, terminal_event: AgentEvent) -> list[AgentEvent]:
        return [
            *missing_tool_return_events(
                outstanding_tool_calls=self.calls,
                terminal_event=terminal_event,
            ),
            terminal_event,
        ]

    def before(self, event: AgentEvent) -> list[AgentEvent]:
        if event.type not in TERMINAL_TOOL_EVENT_TYPES:
            return []
        return missing_tool_return_events(
            outstanding_tool_calls=self.calls,
            terminal_event=event,
        )


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
                        if tool_name in {"ask_user", "request_approval"}
                        else {}
                    ),
                },
                metadata={
                    "synthetic_tool_return": True,
                    "terminal_event": terminal_event.type.value,
                },
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
