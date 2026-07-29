"""Synthetic tool returns emitted when a harness terminates mid-call."""

from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    MessageDraft,
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
        return "Remote harness run stopped before the tool returned a result."
    if event_type == AgentEventType.ERROR:
        return "Remote harness run failed before the tool returned a result."
    if event_type == AgentEventType.REJECTED:
        return "Remote harness run was rejected before the tool returned a result."
    return "Remote harness run completed without a tool return."
