"""What one model response's events are to the run's record.

Pure rules over the events a response produced, kept apart from the loop that
drives the graph: which tool calls are stored (one per id, by the rule
``UniqueToolCallIdsCapability`` applies to what runs), and which of its text is
the run's answer.
"""

from __future__ import annotations

import json

from app.modules.agent.domain.tool_call_ids import (
    call_signature,
    unique_per_call_id,
    unparsed_signature,
)
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    AnswerKind,
    MessageDraft,
    MessageKind,
    MessageRole,
)


def _tool_call_id_of(event: AgentEvent) -> str | None:
    draft = event.data
    if not isinstance(draft, MessageDraft) or draft.kind is not MessageKind.TOOL_CALL:
        return None
    return draft.tool_call_id


def _tool_call_signature(event: AgentEvent) -> str | None:
    draft = event.data
    assert isinstance(draft, MessageDraft)
    return call_signature(draft.tool_name, draft.tool_args)


def _raw_tool_call_signature(event: AgentEvent) -> str:
    draft = event.data
    assert isinstance(draft, MessageDraft)
    return unparsed_signature(draft.tool_name, draft.tool_args)


def _under_call_id(event: AgentEvent, call_id: str) -> AgentEvent:
    draft = event.data
    assert isinstance(draft, MessageDraft)
    return event.model_copy(
        update={"data": draft.model_copy(update={"tool_call_id": call_id})}
    )


def unique_stored_calls(events: list[AgentEvent]) -> list[AgentEvent]:
    """The stored half of `UniqueToolCallIdsCapability`, by the same rule: a
    call the provider repeated under one id is stored once, as it runs once,
    and a different call under a taken id is stored under the id it ran under.
    """
    kept, _repeats = unique_per_call_id(
        events,
        call_id=_tool_call_id_of,
        signature=_tool_call_signature,
        raw_signature=_raw_tool_call_signature,
        renamed=_under_call_id,
    )
    return kept


def _canonical(value: object) -> str:
    """One spelling per value, so equal arguments and results compare equal."""
    return json.dumps(value, sort_keys=True, default=str)


def _is_open_answer(event: AgentEvent) -> bool:
    """Assistant text nobody has yet said is, or is not, the final answer."""
    draft = event.data
    return (
        event.type is AgentEventType.MESSAGE
        and isinstance(draft, MessageDraft)
        and draft.role is MessageRole.ASSISTANT
        and draft.kind is MessageKind.TEXT
        and draft.state.answer is None
    )


def _not_final(event: AgentEvent) -> AgentEvent:
    draft = event.data
    assert isinstance(draft, MessageDraft)
    state = draft.state.model_copy(update={"answer": AnswerKind.NOT_FINAL})
    return event.model_copy(update={"data": draft.model_copy(update={"state": state})})


def settle_response_answers(
    messages: list[AgentEvent],
) -> tuple[list[AgentEvent], list[AgentEvent]]:
    """One finished response's messages: those settled, and those still open.

    Unmarked assistant text is stored as the final answer, and a text part is
    written before the rest of its response arrives -- so a preamble before a
    tool call, and a run that answered and then went on working, each stored a
    final answer the run did not end on. Only the text a run ends on is final:

    - a response that calls a tool goes on, so none of its text is final;
    - otherwise every text but the last is not, and the last -- with whatever
      follows it, so the order holds -- waits for the next node to say whether
      the run ends here.
    """
    open_at = [index for index, event in enumerate(messages) if _is_open_answer(event)]
    if not open_at:
        return messages, []
    calls_a_tool = any(
        isinstance(event.data, MessageDraft)
        and event.data.kind is MessageKind.TOOL_CALL
        for event in messages
    )
    last = None if calls_a_tool else open_at[-1]
    settled = [
        _not_final(event) if index in open_at and index != last else event
        for index, event in enumerate(messages)
    ]
    if last is None:
        return settled, []
    return settled[:last], settled[last:]
